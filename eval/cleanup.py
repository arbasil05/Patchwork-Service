import os
import sys
import json
import redis
import subprocess
import psutil

RESULTS_DIR = os.path.abspath("eval/results")
BACKUP_FILE = os.path.join(RESULTS_DIR, "redis_pool_config_backup.json")
SUB_CSV = os.path.join(RESULTS_DIR, "raw", "submissions.csv")

def run_cleanup():
    print("=== Running Final Evaluation Cleanup ===")
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

    # 1. Stop workers and daemon processes
    print("[Cleanup] Stopping daemon and worker processes...")
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            if any(x in cmd for x in ["run_worker.py", "pool_daemon.py"]):
                if p.pid != os.getpid():
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # 2. Kill study containers
    print("[Cleanup] Killing study containers...")
    cmd = ["docker", "ps", "-a", "--filter", "name=patchwork-django", "--filter", "name=patchwork-express", "--filter", "name=ondemand_", "--format", "{{.ID}}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    cids = [x.strip() for x in res.stdout.strip().splitlines() if x.strip()]
    for cid in cids:
        subprocess.run(["docker", "kill", cid], capture_output=True)
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    print(f"[Cleanup] Cleaned {len(cids)} container(s).")

    # 3. Delete pool keys for both tags
    print("[Cleanup] Deleting pool keys for experiment images...")
    pool_keys = [
        "pool:patchwork-django:latest:idle",
        "pool:patchwork-django:latest:busy",
        "pool:patchwork-express:latest:idle",
        "pool:patchwork-express:latest:busy"
    ]
    r.delete(*pool_keys)

    # 4. Delete idempotency and job_owner keys for recorded UUIDs
    print("[Cleanup] Deleting idempotency and job_owner keys...")
    uuids_to_clean = set()
    if os.path.exists(SUB_CSV):
        try:
            import pandas as pd
            df = pd.read_csv(SUB_CSV)
            if "submission_id" in df.columns:
                uuids_to_clean.update(df["submission_id"].dropna().unique())
        except Exception:
            pass

    for uid in uuids_to_clean:
        r.delete(f"idempotency:{uid}")
        r.delete(f"job_owner:{uid}")
    print(f"[Cleanup] Cleaned {len(uuids_to_clean)} submission keys from Redis.")

    # 5. Restore every pool_config record from backup
    print("[Cleanup] Restoring pool_config records from backup...")
    if os.path.exists(BACKUP_FILE):
        backup = json.load(open(BACKUP_FILE, encoding="utf-8"))
        configs = backup.get("configs", {})
        pool_names = backup.get("pool_configs", [])

        # Reset pool_configs set
        r.delete("pool_configs")
        if pool_names:
            r.sadd("pool_configs", *pool_names)

        for name, data in configs.items():
            key = f"pool_config:{name}"
            r.delete(key)
            if data:
                r.hset(key, mapping=data)
        print(f"[Cleanup] Restored {len(configs)} pool config records: {list(configs.keys())}")

    # Clean RQ worker registrations
    try:
        w_keys = r.keys("rq:worker:*")
        if w_keys:
            r.delete(*w_keys)
        r.delete("rq:workers")
    except Exception:
        pass

    # 6. Verification evidence
    print("\n=== Cleanup Verification Evidence ===")
    ps_res = subprocess.run(["docker", "ps"], capture_output=True, text=True)
    print("docker ps output:")
    print(ps_res.stdout.strip())

    active_pools = r.smembers("pool_configs")
    print(f"\nRedis pool_configs: {active_pools}")
    for p in sorted(active_pools):
        print(f"  pool_config:{p} -> {r.hgetall(f'pool_config:{p}')}")

    print("\n[Cleanup] Final cleanup complete.")

if __name__ == "__main__":
    run_cleanup()
