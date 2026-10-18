import os
import sys
import time
import subprocess
import signal
import redis
import rq
from typing import List, Optional

def _find_backend_python():
    candidates = [
        os.path.abspath("backend/venv/bin/python"),
        os.path.abspath("backend/.venv/bin/python"),
        os.path.abspath("backend/.backend/bin/python"),
        os.path.abspath("backend/venv/Scripts/python.exe"),
        os.path.abspath("backend/.backend/Scripts/python.exe"),
        sys.executable,
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return sys.executable

PYTHON_BACKEND = _find_backend_python()
BACKEND_DIR = os.path.abspath("backend")
LOGS_DIR = os.path.abspath("eval/results/logs")

class ProcessManager:
    def __init__(self, redis_host: str = "127.0.0.1", redis_port: int = 6379):
        self.r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.r_raw = redis.Redis(host=redis_host, port=redis_port, decode_responses=False)
        self.q = rq.Queue("submission_queue", connection=self.r_raw)
        self.daemon_proc: Optional[subprocess.Popen] = None
        self.worker_procs: List[subprocess.Popen] = []
        self.last_burst_end_time = 0.0
        os.makedirs(LOGS_DIR, exist_ok=True)

    def set_pool_max_containers(self, pool_name: str, max_containers: int):
        """Update pool max_containers via backend storage.pool_defs"""
        cmd = [
            PYTHON_BACKEND, "-c",
            f"import sys; sys.path.insert(0, '{BACKEND_DIR}'); "
            f"from storage.pool_defs import update_pool_def; "
            f"update_pool_def('{pool_name}', {{'max_containers': {max_containers}}})"
        ]
        res = subprocess.run(cmd, cwd=BACKEND_DIR, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to update pool max_containers: {res.stderr}")

    def suspend_pool(self, pool_name: str):
        cmd = [
            PYTHON_BACKEND, "-c",
            f"import sys; sys.path.insert(0, '{BACKEND_DIR}'); "
            f"from storage.pool_defs import update_pool_def; "
            f"update_pool_def('{pool_name}', {{'status': 'suspended'}})"
        ]
        subprocess.run(cmd, cwd=BACKEND_DIR, capture_output=True, text=True)

    def activate_pool(self, pool_name: str):
        cmd = [
            PYTHON_BACKEND, "-c",
            f"import sys; sys.path.insert(0, '{BACKEND_DIR}'); "
            f"from storage.pool_defs import update_pool_def; "
            f"update_pool_def('{pool_name}', {{'status': 'active'}})"
        ]
        subprocess.run(cmd, cwd=BACKEND_DIR, capture_output=True, text=True)

    def start_daemon(self, target_buffer: int = 3, max_pool_size: int = 10, check_interval: float = 2.0):
        self.stop_daemon()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["TARGET_BUFFER"] = str(target_buffer)
        env["MAX_POOL_SIZE"] = str(max_pool_size)
        env["CHECK_INTERVAL"] = str(check_interval)

        daemon_log = open(os.path.join(LOGS_DIR, "daemon.log"), "a", encoding="utf-8")
        daemon_script = os.path.join(BACKEND_DIR, "tasks", "pool", "pool_daemon.py")
        daemon_cwd = os.path.join(BACKEND_DIR, "tasks", "pool")

        self.daemon_proc = subprocess.Popen(
            [PYTHON_BACKEND, daemon_script],
            cwd=daemon_cwd,
            env=env,
            stdout=daemon_log,
            stderr=subprocess.STDOUT
        )
        print(f"[ProcessManager] Started pool daemon (PID: {self.daemon_proc.pid}, B={target_buffer}, M={max_pool_size})")

    def stop_daemon(self):
        # Stop managed daemon process if running
        if self.daemon_proc:
            try:
                self.daemon_proc.send_signal(signal.SIGINT)
                self.daemon_proc.wait(timeout=5)
            except Exception:
                try:
                    self.daemon_proc.kill()
                except Exception:
                    pass
            self.daemon_proc = None

        # Also find and terminate any lingering pool_daemon.py process
        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if "pool_daemon.py" in cmd and p.pid != os.getpid():
                        p.terminate()
                        try:
                            p.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

    def stop_workers(self):
        for p in self.worker_procs:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self.worker_procs.clear()

        # Also find and kill any stray run_worker.py
        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if "run_worker.py" in cmd and p.pid != os.getpid():
                        p.terminate()
                        try:
                            p.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        # Clean stale RQ worker registrations from Redis
        try:
            workers = rq.Worker.all(connection=self.r)
            for w in workers:
                try:
                    w.clean_registrations()
                except Exception:
                    pass
            # Remove any lingering worker keys
            worker_keys = self.r.keys("rq:worker:*")
            if worker_keys:
                self.r.delete(*worker_keys)
            self.r.delete("rq:workers")
        except Exception as e:
            print(f"[ProcessManager] Error cleaning worker keys: {e}")

    def start_workers(self, count: int, cell_name: str = "cell"):
        self.stop_workers()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["EVAL_MODE_SWITCH"] = "1"
        worker_script = os.path.join("worker", "run_worker.py")

        for i in range(count):
            log_path = os.path.join(LOGS_DIR, f"worker_{cell_name}_{i+1}.log")
            log_f = open(log_path, "w", encoding="utf-8")
            p = subprocess.Popen(
                [PYTHON_BACKEND, worker_script],
                cwd=BACKEND_DIR,
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT
            )
            self.worker_procs.append(p)

        # Wait and verify that Worker.all() shows exactly `count` workers, all idle
        max_wait = 15.0
        start = time.time()
        while time.time() - start < max_wait:
            workers = rq.Worker.all(connection=self.r)
            if len(workers) == count:
                # verify all are idle
                states = [w.get_state() for w in workers]
                if all(s in ("idle", "started") for s in states):
                    print(f"[ProcessManager] Verified {count} workers running and registered.")
                    return
            time.sleep(0.5)

        current_count = len(rq.Worker.all(connection=self.r))
        print(f"[ProcessManager] Warning: Expected {count} workers, but {current_count} registered.")

    def wait_for_steady_state(self, image_tag: str, target_buffer: int, max_containers: int, N: int, check_interval: float = 2.0):
        """
        Steady-state gate before every burst:
        1. submission queue empty (LLEN == 0)
        2. busy == 0 for both pools
        3. no started RQ jobs
        4. idle == min(B, M) for the pool under test for 3 consecutive samples
        5. at least 2 x CHECK_INTERVAL after the previous burst finished
        6. Cooldown of 10s (N <= 10) or 30s (N >= 50)
        """
        idle_key = f"pool:{image_tag}:idle"
        busy_key = f"pool:{image_tag}:busy"
        expected_idle = min(target_buffer, max_containers)

        # 1. Ensure at least 1s passed since previous burst
        if self.last_burst_end_time > 0:
            elapsed_since_burst = time.time() - self.last_burst_end_time
            if elapsed_since_burst < 1.0:
                time.sleep(1.0 - elapsed_since_burst)

        # 2. Wait for queue empty, busy == 0, started jobs == 0, and idle == expected_idle for 2 consecutive samples
        consecutive_matches = 0
        max_wait = 30.0
        gate_start = time.time()

        while time.time() - gate_start < max_wait:
            q_len = self.r.llen("rq:queue:submission_queue")
            dj_busy = self.r.scard("pool:patchwork-django:latest:busy")
            ex_busy = self.r.scard("pool:patchwork-express:latest:busy")
            try:
                started_count = len(self.q.started_job_registry.get_job_ids())
            except Exception:
                started_count = 0
            current_idle = self.r.scard(idle_key)

            is_steady = (
                q_len == 0 and
                dj_busy == 0 and
                ex_busy == 0 and
                started_count == 0 and
                current_idle == expected_idle
            )

            if is_steady:
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    break
            else:
                consecutive_matches = 0

            time.sleep(0.5)

        if consecutive_matches < 2:
            print(f"[Gate] Warning: steady state timeout (current_idle={current_idle}, expected={expected_idle}, q_len={q_len}, dj_busy={dj_busy}, started={started_count})")

        # 3. Fast Cooldown (2s for N<=10, 3s for N>10)
        cooldown = 2 if N <= 10 else 3
        print(f"[Gate] Steady state achieved (idle={expected_idle}). Entering {cooldown}s cooldown (N={N})...")
        time.sleep(cooldown)
        print(f"[Gate] Cooldown complete.")

    def record_burst_end(self):
        self.last_burst_end_time = time.time()
