import asyncio
import os
import sys
import uuid
import json
import httpx
import subprocess

sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager

def get_running_container_ids():
    cmd = ["docker", "ps", "-q"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return set(res.stdout.strip().splitlines())

async def run_v3():
    print("=== Running Validation Gate V3: Mode Switch and HostConfig Match ===")
    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()

    from process_manager import PYTHON_BACKEND
    BACKEND_DIR = os.path.abspath("backend")

    # 1. Compare HostConfig between pool container and on-demand container for both Django and Express
    for img in ["patchwork-django:latest", "patchwork-express:latest"]:
        cmd = [
            PYTHON_BACKEND, "-c",
            f"import sys; sys.path.insert(0, '{BACKEND_DIR}'); "
            f"from tasks.pool.pool_manager import create_container; "
            f"c_pool = create_container('{img}', register_in_pool=True); "
            f"c_ondemand = create_container('{img}', register_in_pool=False); "
            f"print('CONTAINER_IDS:' + c_pool + ',' + c_ondemand)"
        ]
        res = subprocess.run(cmd, cwd=BACKEND_DIR, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[V3] Failed to create comparison containers for {img}: {res.stderr}")
            return False

        line = [l for l in res.stdout.strip().splitlines() if l.startswith("CONTAINER_IDS:")][0]
        c_pool_id, c_ondemand_id = line.replace("CONTAINER_IDS:", "").split(",")

        def _get_host_config(cid):
            insp = subprocess.run(["docker", "inspect", cid], capture_output=True, text=True)
            data = json.loads(insp.stdout)
            return data[0]["HostConfig"]

        cfg_pool = _get_host_config(c_pool_id)
        cfg_ondemand = _get_host_config(c_ondemand_id)

        keys_to_compare = ["NetworkMode", "Memory", "PidsLimit", "ReadonlyRootfs", "Tmpfs"]
        mismatches = []
        for k in keys_to_compare:
            v_p = cfg_pool.get(k)
            v_o = cfg_ondemand.get(k)
            if v_p != v_o:
                mismatches.append((k, v_p, v_o))

        # Cleanup both comparison containers
        subprocess.run(["docker", "kill", c_pool_id], capture_output=True)
        subprocess.run(["docker", "kill", c_ondemand_id], capture_output=True)

        if mismatches:
            print(f"[V3] HostConfig mismatch for {img}: {mismatches}")
            return False
        else:
            print(f"[V3] HostConfig for {img} matches perfectly: {keys_to_compare}")

    # 2. Check docker ps before and after on-demand submission
    pm.start_workers(1, cell_name="v3")
    await asyncio.sleep(2)

    ps_before = get_running_container_ids()
    print(f"[V3] Running containers before on-demand: {len(ps_before)}")

    with open("eval/payloads/django_pass.json") as f:
        p_ondemand = json.load(f)

    job_id = str(uuid.uuid4())
    p_ondemand["idempotency_key"] = job_id
    p_ondemand["metadata"] = {"execution_mode": "on_demand"}
    p_ondemand["callback_url"] = f"http://{receiver.host}:{receiver.port}/webhook"

    receiver.register_job(job_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("http://127.0.0.1:8000/v1/execute", json=p_ondemand)
        if resp.status_code != 202:
            print(f"[V3] POST failed with {resp.status_code}")
            return False

        wh = await receiver.wait_for_job(job_id, timeout=30.0)
        if not wh:
            print("[V3] Webhook timed out for on_demand submission")
            return False

        # Wait 1s for container destruction in finally block
        await asyncio.sleep(1.0)
        ps_after = get_running_container_ids()
        print(f"[V3] Running containers after on-demand: {len(ps_after)}")

        if ps_before != ps_after:
            print(f"[V3] Container leak! Before={ps_before}, After={ps_after}")
            return False
        print("[V3] Exact container destruction verified: docker ps before == docker ps after!")

        # Verify execution_mode in metadata
        wh_meta = wh["payload"].get("metadata", {})
        if wh_meta.get("execution_mode") != "on_demand":
            print(f"[V3] metadata.execution_mode mismatch: {wh_meta.get('execution_mode')}")
            return False
        if not wh_meta.get("container_create_time"):
            print(f"[V3] Missing container_create_time in on-demand metrics: {wh_meta}")
            return False
        print(f"[V3] on-demand metadata verified: create_time={wh_meta.get('container_create_time'):.4f}s")

        # 3. Verify pool submission is unaffected
        job_id_pool = str(uuid.uuid4())
        p_pool = json.load(open("eval/payloads/django_pass.json"))
        p_pool["idempotency_key"] = job_id_pool
        p_pool["metadata"] = {"execution_mode": "pool"}
        p_pool["callback_url"] = f"http://{receiver.host}:{receiver.port}/webhook"

        receiver.register_job(job_id_pool)
        resp_pool = await client.post("http://127.0.0.1:8000/v1/execute", json=p_pool)
        wh_pool = await receiver.wait_for_job(job_id_pool, timeout=30.0)
        if not wh_pool:
            print("[V3] Webhook timed out for pool submission")
            return False

        wh_pool_meta = wh_pool["payload"].get("metadata", {})
        if wh_pool_meta.get("execution_mode") != "pool":
            print(f"[V3] Pool metadata.execution_mode mismatch: {wh_pool_meta}")
            return False
        if wh_pool_meta.get("container_acquire_time") is None:
            print(f"[V3] Missing container_acquire_time in pool metrics: {wh_pool_meta}")
            return False
        print(f"[V3] Pool submission verified: acquire_time={wh_pool_meta.get('container_acquire_time'):.4f}s, pool_hit={wh_pool_meta.get('pool_hit')}")

    await receiver.stop()
    pm.stop_workers()

    print("\n=== Gate V3 Result: PASS ===")
    os.makedirs("eval/results/gates", exist_ok=True)
    with open("eval/results/gates/v3.json", "w", encoding="utf-8") as f:
        json.dump({"passed": True}, f, indent=2)

    return True

if __name__ == "__main__":
    success = asyncio.run(run_v3())
    sys.exit(0 if success else 1)
