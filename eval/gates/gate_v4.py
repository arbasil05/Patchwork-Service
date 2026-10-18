import asyncio
import os
import sys
import uuid
import json
import time
import httpx
import numpy as np
import subprocess
import signal
import redis
import rq

sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager, PYTHON_BACKEND

BACKEND_DIR = os.path.abspath("backend")
PRE_CHANGE_DIR = os.path.abspath("eval/pre_change")

def start_custom_worker(is_pre_change: bool = False, worker_log_path: str = "eval/results/logs/worker_v4.log"):
    env = os.environ.copy()
    if is_pre_change:
        env["PYTHONPATH"] = os.pathsep.join([PRE_CHANGE_DIR, BACKEND_DIR])
        worker_code = f"""
import sys, os
sys.path.insert(0, r'{PRE_CHANGE_DIR}')
sys.path.append(r'{BACKEND_DIR}')
from redis import Redis as SyncRedis
from rq import SimpleWorker
from config.settings import settings
redis_con = SyncRedis(host=settings.REDIS_HOST, port=6379)
worker = SimpleWorker(queues=['submission_queue'], connection=redis_con)
worker.work()
"""
    else:
        env["EVAL_MODE_SWITCH"] = "1"
        worker_code = f"""
import sys, os
sys.path.insert(0, r'{BACKEND_DIR}')
from redis import Redis as SyncRedis
from rq import SimpleWorker
from config.settings import settings
redis_con = SyncRedis(host=settings.REDIS_HOST, port=6379)
worker = SimpleWorker(queues=['submission_queue'], connection=redis_con)
worker.work()
"""
    log_f = open(worker_log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([PYTHON_BACKEND, "-c", worker_code], cwd=BACKEND_DIR, env=env, stdout=log_f, stderr=subprocess.STDOUT)
    return proc

def stop_custom_worker(proc):
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    # Clean stale RQ worker registrations
    r = redis.Redis(host="127.0.0.1", port=6379)
    try:
        workers = rq.Worker.all(connection=r)
        for w in workers:
            try:
                w.clean_registrations()
            except Exception:
                pass
        w_keys = r.keys("rq:worker:*")
        if w_keys:
            r.delete(*w_keys)
        r.delete("rq:workers")
    except Exception:
        pass

async def run_30_sequential(client, receiver, desc="run"):
    with open("eval/payloads/django_pass.json") as f:
        payload_base = json.load(f)

    worker_times = []
    for i in range(30):
        job_id = str(uuid.uuid4())
        p = dict(payload_base)
        p["idempotency_key"] = job_id
        p["metadata"] = {"execution_mode": "pool"}
        p["callback_url"] = f"http://{receiver.host}:{receiver.port}/webhook"

        receiver.register_job(job_id)
        resp = await client.post("http://127.0.0.1:8000/v1/execute", json=p)
        if resp.status_code != 202:
            print(f"[{desc}] Job {i+1} failed to submit: {resp.status_code}")
            continue

        wh = await receiver.wait_for_job(job_id, timeout=30.0)
        if not wh:
            print(f"[{desc}] Job {i+1} webhook timed out")
            continue

        meta = wh["payload"].get("metadata", {})
        t_worker = meta.get("total_worker_time")
        if t_worker is not None:
            worker_times.append(float(t_worker))
        else:
            print(f"[{desc}] Job {i+1} missing total_worker_time: {meta}")

        # Pause between sequential submissions to prevent CPU throttling
        await asyncio.sleep(1.0)

    return worker_times

async def run_v4():
    print("=== Running Validation Gate V4: Instrumentation Overhead ===")
    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()

    limits = httpx.Limits(max_keepalive_connections=None, max_connections=None)
    async with httpx.AsyncClient(limits=limits, timeout=60.0) as client:
        # Phase 1: Pre-change worker (30 sequential N=1)
        print("\n[V4] Phase 1: Running 30 sequential submissions on pre-change code...")
        pm.stop_workers()
        worker_pre = start_custom_worker(is_pre_change=True, worker_log_path="eval/results/logs/worker_v4_pre.log")
        await asyncio.sleep(3)

        times_pre = await run_30_sequential(client, receiver, desc="Pre-change")
        stop_custom_worker(worker_pre)
        median_pre = float(np.median(times_pre)) if times_pre else 0.0
        print(f"[V4] Pre-change runs: {len(times_pre)}/30 successful | Median total_worker_time: {median_pre:.4f} s")

        # 10s cooldown between phases to return CPU to baseline temperature
        print("[V4] Cooling down for 10s between phases...")
        await asyncio.sleep(10)

        # Phase 2: Frozen commit worker (30 sequential N=1)
        print("\n[V4] Phase 2: Running 30 sequential submissions on frozen commit code...")
        pm.stop_workers()
        worker_frozen = start_custom_worker(is_pre_change=False, worker_log_path="eval/results/logs/worker_v4_frozen.log")
        await asyncio.sleep(3)

        times_frozen = await run_30_sequential(client, receiver, desc="Frozen")
        stop_custom_worker(worker_frozen)
        median_frozen = float(np.median(times_frozen)) if times_frozen else 0.0
        print(f"[V4] Frozen runs: {len(times_frozen)}/30 successful | Median total_worker_time: {median_frozen:.4f} s")

    await receiver.stop()

    if median_pre > 0:
        diff_pct = abs(median_frozen - median_pre) / median_pre * 100.0
    else:
        diff_pct = 999.0

    passed = (diff_pct < 5.0 and len(times_pre) >= 28 and len(times_frozen) >= 28)
    print(f"\n[V4] Difference: {diff_pct:.2f} % (threshold < 5.0 %)")
    print(f"=== Gate V4 Result: {'PASS' if passed else 'FAIL'} ===")

    os.makedirs("eval/results/gates", exist_ok=True)
    with open("eval/results/gates/v4.json", "w", encoding="utf-8") as f:
        json.dump({
            "passed": passed,
            "median_pre_change_s": round(median_pre, 6),
            "median_frozen_s": round(median_frozen, 6),
            "diff_pct": round(diff_pct, 4),
            "pre_change_counts": len(times_pre),
            "frozen_counts": len(times_frozen),
            "times_pre": times_pre,
            "times_frozen": times_frozen
        }, f, indent=2)

    return passed

if __name__ == "__main__":
    success = asyncio.run(run_v4())
    sys.exit(0 if success else 1)
