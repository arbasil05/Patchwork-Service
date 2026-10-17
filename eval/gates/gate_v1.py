import asyncio
import os
import sys
import uuid
import json
import httpx

# Ensure eval is on sys.path
sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager

async def run_v1():
    print("=== Running Validation Gate V1 ===")
    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()

    # Start 1 worker
    pm.start_workers(1, cell_name="v1")
    await asyncio.sleep(2)

    with open("eval/payloads/django_pass.json") as f:
        dj_pass = json.load(f)
    with open("eval/payloads/django_fail.json") as f:
        dj_fail = json.load(f)
    with open("eval/payloads/express_pass.json") as f:
        ex_pass = json.load(f)
    with open("eval/payloads/express_fail.json") as f:
        ex_fail = json.load(f)

    tests = [
        ("Django Passing", dj_pass, 0),
        ("Django Failing", dj_fail, 1),
        ("Express Passing", ex_pass, 0),
        ("Express Failing", ex_fail, 1),
    ]

    results = {}
    passed_all = True

    async with httpx.AsyncClient(timeout=30.0) as client:
        for name, payload, expected_exit in tests:
            job_id = str(uuid.uuid4())
            payload["idempotency_key"] = job_id
            payload["callback_url"] = f"http://{receiver.host}:{receiver.port}/webhook"
            receiver.register_job(job_id)

            resp = await client.post("http://127.0.0.1:8000/v1/execute", json=payload)
            if resp.status_code != 202:
                print(f"[V1] {name}: POST failed with {resp.status_code}")
                passed_all = False
                continue

            wh = await receiver.wait_for_job(job_id, timeout=30.0)
            if not wh:
                print(f"[V1] {name}: Webhook timed out")
                passed_all = False
                continue

            p = wh["payload"]
            actual_status = p.get("status")
            actual_exit = p.get("exit_code")

            # Check status endpoint (polling until RQ finishes teardown)
            api_status = None
            api_exit = None
            for _ in range(20):
                st_resp = await client.get(f"http://127.0.0.1:8000/v1/status/{job_id}")
                if st_resp.status_code == 200:
                    st_data = st_resp.json()
                    api_status = st_data.get("status")
                    api_exit = st_data.get("exit_code")
                    if api_status in ("completed", "failed"):
                        break
                await asyncio.sleep(0.2)

            ok = (actual_status == "completed" and actual_exit == expected_exit and api_status == "completed" and api_exit == expected_exit)
            print(f"[V1] {name}: ok={ok} | webhook status={actual_status}, exit_code={actual_exit} | api status={api_status}, exit_code={api_exit}")
            results[name] = {
                "passed": ok,
                "webhook_status": actual_status,
                "webhook_exit_code": actual_exit,
                "api_status": api_status,
                "api_exit_code": api_exit
            }
            if not ok:
                passed_all = False

    await receiver.stop()
    pm.stop_workers()

    print(f"\n=== Gate V1 Result: {'PASS' if passed_all else 'FAIL'} ===")
    os.makedirs("eval/results/gates", exist_ok=True)
    with open("eval/results/gates/v1.json", "w", encoding="utf-8") as f:
        json.dump({"passed": passed_all, "results": results}, f, indent=2)

    return passed_all

if __name__ == "__main__":
    success = asyncio.run(run_v1())
    sys.exit(0 if success else 1)
