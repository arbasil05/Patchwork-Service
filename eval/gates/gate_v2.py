import asyncio
import os
import sys
import uuid
import json
import httpx
import redis
from rq.job import Job

# Ensure eval is on sys.path
sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager

async def run_v2():
    print("=== Running Validation Gate V2: Webhook vs RQ ended_at (20 jobs) ===")
    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()

    r_raw = redis.Redis(host="127.0.0.1", port=6379)

    # Start 2 workers
    pm.start_workers(2, cell_name="v2")
    await asyncio.sleep(2)

    with open("eval/payloads/django_pass.json") as f:
        payload_base = json.load(f)

    results = []
    passed_count = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(20):
            job_id = str(uuid.uuid4())
            p = dict(payload_base)
            p["idempotency_key"] = job_id
            p["callback_url"] = f"http://{receiver.host}:{receiver.port}/webhook"
            p["metadata"] = {"execution_mode": "pool"}

            receiver.register_job(job_id)
            resp = await client.post("http://127.0.0.1:8000/v1/execute", json=p)
            if resp.status_code != 202:
                print(f"[V2] Job {i+1}: POST failed with {resp.status_code}")
                continue

            wh = await receiver.wait_for_job(job_id, timeout=30.0)
            if not wh:
                print(f"[V2] Job {i+1}: Webhook timed out")
                continue

            t_wh_wall = wh["received_at_wall"]

            # Wait briefly for worker to complete finally block and RQ to record ended_at
            rq_ended_at = None
            for _ in range(20):
                await asyncio.sleep(0.1)
                try:
                    rq_job = Job.fetch(job_id, connection=r_raw)
                    if rq_job.ended_at:
                        rq_ended_at = rq_job.ended_at.timestamp()
                        break
                except Exception:
                    pass

            if rq_ended_at is not None:
                delta = rq_ended_at - t_wh_wall
                print(f"[V2] Job {i+1:2d} ({job_id[:8]}): webhook={t_wh_wall:.4f}, rq_ended={rq_ended_at:.4f}, diff (ended - wh)={delta*1000:+.2f} ms")
                results.append({
                    "job_id": job_id,
                    "t_webhook_wall": t_wh_wall,
                    "rq_ended_at": rq_ended_at,
                    "delta_ms": round(delta * 1000, 2)
                })
                passed_count += 1
            else:
                print(f"[V2] Job {i+1}: Failed to fetch RQ ended_at")

    await receiver.stop()
    pm.stop_workers()

    all_passed = (passed_count == 20)
    print(f"\n=== Gate V2 Result: {'PASS' if all_passed else 'FAIL'} ({passed_count}/20 jobs matched) ===")

    deltas = [r["delta_ms"] for r in results]
    if deltas:
        print(f"[V2] Summary delta (RQ ended_at - webhook receipt): median={sorted(deltas)[len(deltas)//2]:.2f} ms, min={min(deltas):.2f} ms, max={max(deltas):.2f} ms")

    os.makedirs("eval/results/gates", exist_ok=True)
    with open("eval/results/gates/v2.json", "w", encoding="utf-8") as f:
        json.dump({"passed": all_passed, "total": 20, "matched": passed_count, "results": results}, f, indent=2)

    return all_passed

if __name__ == "__main__":
    success = asyncio.run(run_v2())
    sys.exit(0 if success else 1)
