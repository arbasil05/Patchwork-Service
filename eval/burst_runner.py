import asyncio
import time
import uuid
import json
import os
import copy
import httpx
import numpy as np
import redis
from rq.job import Job
from typing import List, Dict, Any, Optional

class BurstRunner:
    def __init__(self, api_url: str = "http://127.0.0.1:8000", redis_host: str = "127.0.0.1", redis_port: int = 6379):
        self.api_url = api_url
        self.r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.r_raw = redis.Redis(host=redis_host, port=redis_port)
        self.all_recorded_uuids: List[str] = []

        # Load payload templates
        with open("eval/payloads/django_pass.json", "r", encoding="utf-8") as f:
            self.django_payload = json.load(f)
        with open("eval/payloads/express_pass.json", "r", encoding="utf-8") as f:
            self.express_payload = json.load(f)

    async def run_burst(
        self,
        experiment: Any,
        cell: str,
        burst_id: str,
        mode: str,
        workload: str,
        N: int,
        W: int,
        B: int,
        M: int,
        repetition: int,
        is_warmup: bool,
        receiver: Any,
        sampler: Any
    ) -> Dict[str, Any]:
        submissions_data: List[Dict[str, Any]] = []
        barrier = asyncio.Event()

        # Build payloads
        tasks_payloads = []
        for i in range(N):
            job_uuid = str(uuid.uuid4())
            self.all_recorded_uuids.append(job_uuid)

            if workload == "django":
                p = copy.deepcopy(self.django_payload)
                expected_exit = 0
            elif workload == "express":
                p = copy.deepcopy(self.express_payload)
                expected_exit = 0
            elif workload == "mixed":
                # Half Django, half Express
                if i < N // 2:
                    p = copy.deepcopy(self.django_payload)
                    expected_exit = 0
                else:
                    p = copy.deepcopy(self.express_payload)
                    expected_exit = 0
            else:
                p = copy.deepcopy(self.django_payload)
                expected_exit = 0

            p["idempotency_key"] = job_uuid
            p["metadata"] = {"execution_mode": mode}
            p["callback_url"] = f"http://{receiver.host}:{receiver.port}/webhook"

            receiver.register_job(job_uuid)
            tasks_payloads.append((job_uuid, p, expected_exit))

        # Start sampler
        sampler.start()
        burst_start_time = time.perf_counter()

        async def _submit_one(job_uuid: str, payload: dict, expected_exit: int, client: httpx.AsyncClient):
            await barrier.wait()
            t_submit = time.perf_counter()
            post_rtt = 0.0
            resp_status = 0
            try:
                resp = await client.post(f"{self.api_url}/v1/execute", json=payload, timeout=10.0)
                resp_status = resp.status_code
                post_rtt = time.perf_counter() - t_submit
            except Exception as e:
                return {
                    "submission_id": job_uuid,
                    "t_submit": t_submit,
                    "post_rtt": post_rtt,
                    "t_complete": time.perf_counter(),
                    "completion_source": "error",
                    "status": "failed",
                    "exit_code": -1,
                    "expected_exit_code": expected_exit,
                    "correct": False,
                    "error_text": f"POST failed: {e}",
                    "worker_metrics": {}
                }

            if resp_status != 202:
                return {
                    "submission_id": job_uuid,
                    "t_submit": t_submit,
                    "post_rtt": post_rtt,
                    "t_complete": time.perf_counter(),
                    "completion_source": "error",
                    "status": "failed",
                    "exit_code": -1,
                    "expected_exit_code": expected_exit,
                    "correct": False,
                    "error_text": f"POST returned {resp_status}",
                    "worker_metrics": {}
                }

            # Wait for webhook with scaled timeout for burst size
            job_timeout = max(90.0, float(N) * 2.5)
            wh_data = await receiver.wait_for_job(job_uuid, timeout=job_timeout)
            if wh_data:
                t_complete = wh_data["received_at"]
                wh_payload = wh_data["payload"]
                st = wh_payload.get("status", "failed")
                exit_code = wh_payload.get("exit_code")
                error_txt = wh_payload.get("stderr") or ""
                worker_metrics = wh_payload.get("metadata", {})
                correct = (exit_code == expected_exit)
                return {
                    "submission_id": job_uuid,
                    "t_submit": t_submit,
                    "post_rtt": post_rtt,
                    "t_complete": t_complete,
                    "completion_source": "webhook",
                    "status": st,
                    "exit_code": exit_code,
                    "expected_exit_code": expected_exit,
                    "correct": correct,
                    "error_text": error_txt,
                    "worker_metrics": worker_metrics
                }

            # Fallback to polling /v1/status/{id}
            poll_start = time.perf_counter()
            while time.perf_counter() - poll_start < 45.0:
                await asyncio.sleep(2.0)
                try:
                    s_resp = await client.get(f"{self.api_url}/v1/status/{job_uuid}", timeout=5.0)
                    if s_resp.status_code == 200:
                        s_data = s_resp.json()
                        st = s_data.get("status")
                        if st in ("completed", "failed"):
                            t_complete = time.perf_counter()
                            exit_code = s_data.get("exit_code")
                            error_txt = s_data.get("stderr") or ""
                            worker_metrics = s_data.get("metadata", {})
                            correct = (exit_code == expected_exit)
                            return {
                                "submission_id": job_uuid,
                                "t_submit": t_submit,
                                "post_rtt": post_rtt,
                                "t_complete": t_complete,
                                "completion_source": "poll",
                                "status": st,
                                "exit_code": exit_code,
                                "expected_exit_code": expected_exit,
                                "correct": correct,
                                "error_text": error_txt,
                                "worker_metrics": worker_metrics
                            }
                except Exception:
                    pass

            # Timeout completely
            return {
                "submission_id": job_uuid,
                "t_submit": t_submit,
                "post_rtt": post_rtt,
                "t_complete": time.perf_counter(),
                "completion_source": "timeout",
                "status": "failed",
                "exit_code": -1,
                "expected_exit_code": expected_exit,
                "correct": False,
                "error_text": f"Timeout: No webhook or status update within {job_timeout + 45.0}s",
                "worker_metrics": {}
            }

        # Run concurrent requests
        client_timeout = max(180.0, float(N) * 3.5)
        limits = httpx.Limits(max_keepalive_connections=None, max_connections=None)
        async with httpx.AsyncClient(limits=limits, timeout=client_timeout) as client:
            tasks = [_submit_one(u, p, e, client) for (u, p, e) in tasks_payloads]
            # Release barrier
            barrier.set()
            results = await asyncio.gather(*tasks)

        burst_end_time = time.perf_counter()
        burst_duration = burst_end_time - burst_start_time

        # Stop sampler
        await sampler.stop()
        sampler_summary = sampler.get_summary()

        # Fetch RQ metadata from Redis
        for res in results:
            job_id = res["submission_id"]
            rq_enqueued_at = ""
            rq_started_at = ""
            rq_ended_at = ""
            try:
                rq_job = Job.fetch(job_id, connection=self.r_raw)
                if rq_job.enqueued_at:
                    rq_enqueued_at = rq_job.enqueued_at.timestamp()
                if rq_job.started_at:
                    rq_started_at = rq_job.started_at.timestamp()
                if rq_job.ended_at:
                    rq_ended_at = rq_job.ended_at.timestamp()

                # If worker_metrics missing container_destroy_time or others, pull from job.result
                if rq_job.result and isinstance(rq_job.result, dict):
                    res_metrics = rq_job.result.get("metrics", {})
                    for k, v in res_metrics.items():
                        if k not in res["worker_metrics"] or res["worker_metrics"][k] is None:
                            res["worker_metrics"][k] = v
            except Exception:
                pass

            wm = res["worker_metrics"]
            e2e = res["t_complete"] - res["t_submit"]

            sub_row = {
                "burst_id": burst_id,
                "experiment": experiment,
                "cell": cell,
                "mode": mode,
                "workload": workload,
                "N": N,
                "W": W,
                "B": B,
                "M": M,
                "repetition": repetition,
                "is_warmup": int(is_warmup),
                "submission_id": job_id,
                "completion_source": res["completion_source"],
                "client_submit_s": round(res["t_submit"], 6),
                "e2e_s": round(e2e, 6),
                "status": res["status"],
                "exit_code": res["exit_code"],
                "expected_exit_code": res["expected_exit_code"],
                "correct": int(res["correct"]),
                "error_text": str(res["error_text"]).replace(",", ";").replace("\n", " "),
                "workspace_creation_time": wm.get("workspace_creation_time"),
                "execution_time": wm.get("execution_time"),
                "workspace_cleanup_time": wm.get("workspace_cleanup_time"),
                "total_worker_time": wm.get("total_worker_time"),
                "queue_delay": wm.get("queue_delay"),
                "container_acquire_time": wm.get("container_acquire_time"),
                "pool_hit": 1 if wm.get("pool_hit") is True else (0 if wm.get("pool_hit") is False else ""),
                "container_create_time": wm.get("container_create_time"),
                "container_destroy_time": wm.get("container_destroy_time"),
                "rq_enqueued_at": rq_enqueued_at,
                "rq_started_at": rq_started_at,
                "rq_ended_at": rq_ended_at
            }
            submissions_data.append(sub_row)

        # Write submissions to CSV
        os.makedirs("eval/results/raw", exist_ok=True)
        sub_csv = "eval/results/raw/submissions.csv"
        sub_headers = [
            "burst_id", "experiment", "cell", "mode", "workload", "N", "W", "B", "M",
            "repetition", "is_warmup", "submission_id", "completion_source", "client_submit_s",
            "e2e_s", "status", "exit_code", "expected_exit_code", "correct", "error_text",
            "workspace_creation_time", "execution_time", "workspace_cleanup_time", "total_worker_time",
            "queue_delay", "container_acquire_time", "pool_hit", "container_create_time",
            "container_destroy_time", "rq_enqueued_at", "rq_started_at", "rq_ended_at"
        ]
        write_sub_header = not os.path.exists(sub_csv) or os.path.getsize(sub_csv) == 0
        with open(sub_csv, "a", encoding="utf-8") as f:
            if write_sub_header:
                f.write(",".join(sub_headers) + "\n")
            for row in submissions_data:
                f.write(",".join("" if row[h] is None else str(row[h]) for h in sub_headers) + "\n")

        # Compute burst metrics
        valid_e2e = [row["e2e_s"] for row in submissions_data if row["status"] == "completed" and row["correct"] == 1]
        success_count = len(valid_e2e)
        failed_count = N - success_count
        success_rate = round(success_count / N, 4) if N > 0 else 0.0

        p50 = round(float(np.percentile(valid_e2e, 50)), 6) if valid_e2e else None
        p95 = round(float(np.percentile(valid_e2e, 95)), 6) if valid_e2e else None
        p99 = round(float(np.percentile(valid_e2e, 99)), 6) if valid_e2e else None
        throughput = round(N / burst_duration, 4) if burst_duration > 0 else 0.0

        burst_row = {
            "burst_id": burst_id,
            "experiment": experiment,
            "cell": cell,
            "mode": mode,
            "workload": workload,
            "N": N,
            "W": W,
            "B": B,
            "M": M,
            "repetition": repetition,
            "is_warmup": int(is_warmup),
            "burst_duration_s": round(burst_duration, 4),
            "throughput_req_per_s": throughput,
            "count_total": N,
            "count_success": success_count,
            "count_failed": failed_count,
            "p50_e2e_s": p50,
            "p95_e2e_s": p95,
            "p99_e2e_s": p99,
            "success_rate": success_rate,
            "host_cpu_mean": sampler_summary["host_cpu_mean"],
            "host_cpu_max": sampler_summary["host_cpu_max"],
            "min_free_mem_mb": sampler_summary["min_free_mem_mb"],
            "throttled": int(sampler_summary["throttled"]),
            "oom_killed": int(sampler_summary["oom_killed"])
        }

        burst_csv = "eval/results/raw/bursts.csv"
        burst_headers = [
            "burst_id", "experiment", "cell", "mode", "workload", "N", "W", "B", "M",
            "repetition", "is_warmup", "burst_duration_s", "throughput_req_per_s",
            "count_total", "count_success", "count_failed", "p50_e2e_s", "p95_e2e_s",
            "p99_e2e_s", "success_rate", "host_cpu_mean", "host_cpu_max",
            "min_free_mem_mb", "throttled", "oom_killed"
        ]
        write_burst_header = not os.path.exists(burst_csv) or os.path.getsize(burst_csv) == 0
        with open(burst_csv, "a", encoding="utf-8") as f:
            if write_burst_header:
                f.write(",".join(burst_headers) + "\n")
            f.write(",".join("" if burst_row[h] is None else str(burst_row[h]) for h in burst_headers) + "\n")

        print(f"[Burst] {burst_id} finished in {burst_duration:.2f}s | Success: {success_count}/{N} | p50: {p50}s | p95: {p95}s")
        return burst_row
