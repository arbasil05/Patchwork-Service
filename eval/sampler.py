import asyncio
import time
import os
import sys
import psutil
import redis
import rq
import subprocess
from typing import List, Dict, Any, Optional

class BurstSampler:
    def __init__(self, burst_id: str, redis_host: str = "127.0.0.1", redis_port: int = 6379, output_dir: str = "eval/results/raw/samples"):
        self.burst_id = burst_id
        self.output_dir = output_dir
        self.r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.q = rq.Queue("submission_queue", connection=self.r)
        self.is_running = False
        self.samples: List[Dict[str, Any]] = []
        self.throttled = False
        self.oom_killed = False
        self.min_free_mem_mb = float("inf")
        self.cpu_values: List[float] = []
        self._task: Optional[asyncio.Task] = None
        self.t0 = 0.0

    async def _sample_proc_perf(self):
        """Query CPU performance/throttling every 5s in background"""
        while self.is_running:
            try:
                def _query():
                    if sys.platform == "win32":
                        cmd = ["powershell", "-NoProfile", "-Command", 
                               "(Get-CimInstance -ClassName Win32_PerfFormattedData_Counters_ProcessorInformation -Filter \"Name='_Total'\").PercentProcessorPerformance"]
                        res = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
                        val_str = res.stdout.strip()
                        if val_str:
                            return float(val_str)
                    else:
                        # Linux: check CPU frequency scaling ratio if available
                        try:
                            cur_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
                            max_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"
                            if os.path.exists(cur_path) and os.path.exists(max_path):
                                with open(cur_path) as f1, open(max_path) as f2:
                                    cur = float(f1.read().strip())
                                    max_f = float(f2.read().strip())
                                    if max_f > 0:
                                        return (cur / max_f) * 100.0
                        except Exception:
                            pass
                    return None

                perf_val = await asyncio.to_thread(_query)
                if perf_val is not None and perf_val < 70.0:
                    self.throttled = True
            except Exception:
                pass
            await asyncio.sleep(5.0)

    async def _sample_loop(self):
        self.t0 = time.perf_counter()
        next_1s = self.t0 + 1.0
        cached_cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        cached_avail_mem_mb = mem.available / (1024 * 1024)
        cached_running_containers = 0

        # Launch background task for 5s perf counter
        perf_task = asyncio.create_task(self._sample_proc_perf())

        try:
            while self.is_running:
                now = time.perf_counter()
                rel_t = now - self.t0

                # 1s tick updates
                if now >= next_1s:
                    next_1s = now + 1.0
                    cached_cpu = psutil.cpu_percent(interval=None)
                    self.cpu_values.append(cached_cpu)
                    mem = psutil.virtual_memory()
                    cached_avail_mem_mb = mem.available / (1024 * 1024)
                    if cached_avail_mem_mb < self.min_free_mem_mb:
                        self.min_free_mem_mb = cached_avail_mem_mb

                    # Check running containers count
                    try:
                        c_proc = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=2)
                        cached_running_containers = len([x for x in c_proc.stdout.strip().splitlines() if x])
                    except Exception:
                        pass

                # 250ms tick updates from Redis
                try:
                    pipe = self.r.pipeline()
                    pipe.scard("pool:patchwork-django:latest:idle")
                    pipe.scard("pool:patchwork-django:latest:busy")
                    pipe.scard("pool:patchwork-express:latest:idle")
                    pipe.scard("pool:patchwork-express:latest:busy")
                    pipe.llen("rq:queue:submission_queue")
                    res = pipe.execute()
                    dj_idle, dj_busy, ex_idle, ex_busy, q_len = res
                except Exception:
                    dj_idle = dj_busy = ex_idle = ex_busy = q_len = 0

                try:
                    started_count = self.q.started_job_registry.count
                except Exception:
                    started_count = 0

                self.samples.append({
                    "rel_time_s": round(rel_t, 4),
                    "django_idle": dj_idle,
                    "django_busy": dj_busy,
                    "express_idle": ex_idle,
                    "express_busy": ex_busy,
                    "queue_len": q_len,
                    "started_jobs": started_count,
                    "cpu_percent": cached_cpu,
                    "memory_available_mb": round(cached_avail_mem_mb, 2),
                    "running_containers": cached_running_containers,
                    "throttled": int(self.throttled),
                    "oom_killed": int(self.oom_killed)
                })

                await asyncio.sleep(0.25)
        finally:
            perf_task.cancel()
            try:
                await perf_task
            except asyncio.CancelledError:
                pass

    def start(self):
        self.is_running = True
        self._task = asyncio.create_task(self._sample_loop())

    async def stop(self):
        self.is_running = False
        if self._task:
            await self._task

        # Check docker for any OOM killed containers
        try:
            def _check_oom():
                cmd = ["docker", "ps", "-a", "--filter", "status=exited", "--format", "{{.ID}}"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                ids = [x.strip() for x in res.stdout.strip().splitlines() if x.strip()]
                for cid in ids:
                    inspect_res = subprocess.run(["docker", "inspect", cid, "--format", "{{.State.OOMKilled}}"], capture_output=True, text=True, timeout=2)
                    if inspect_res.stdout.strip() == "true":
                        return True
                return False
            self.oom_killed = await asyncio.to_thread(_check_oom)
        except Exception:
            pass

        # Save CSV
        os.makedirs(self.output_dir, exist_ok=True)
        csv_file = os.path.join(self.output_dir, f"{self.burst_id}.csv")
        headers = [
            "rel_time_s", "django_idle", "django_busy", "express_idle", "express_busy",
            "queue_len", "started_jobs", "cpu_percent", "memory_available_mb",
            "running_containers", "throttled", "oom_killed"
        ]
        with open(csv_file, "w", encoding="utf-8") as f:
            f.write(",".join(headers) + "\n")
            for s in self.samples:
                f.write(",".join(str(s[h]) for h in headers) + "\n")

    def get_summary(self) -> Dict[str, Any]:
        avg_cpu = sum(self.cpu_values) / len(self.cpu_values) if self.cpu_values else 0.0
        max_cpu = max(self.cpu_values) if self.cpu_values else 0.0
        return {
            "host_cpu_mean": round(avg_cpu, 2),
            "host_cpu_max": round(max_cpu, 2),
            "min_free_mem_mb": round(self.min_free_mem_mb, 2) if self.min_free_mem_mb != float("inf") else 0.0,
            "throttled": self.throttled,
            "oom_killed": self.oom_killed
        }
