import asyncio
import os
import sys
import json
import random

sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager
from burst_runner import BurstRunner
from sampler import BurstSampler

async def run_v5():
    print("=== Running Validation Gate V5: Smoke Run (W=4, B=4, M=10, N=10, 3 bursts per mode) ===")
    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()

    W = 4
    B = 4
    M = 10
    N = 10
    workload = "django"

    # Set M=10 and start daemon with B=4, M=10
    pm.set_pool_max_containers("patchwork-django", M)
    pm.set_pool_max_containers("patchwork-express", M)
    pm.start_daemon(target_buffer=B, max_pool_size=M, check_interval=2.0)
    await asyncio.sleep(4)

    # Start W=4 workers
    pm.start_workers(W, cell_name="v5")
    await asyncio.sleep(2)

    runner = BurstRunner(api_url="http://127.0.0.1:8000")

    # 3 bursts pool, 3 bursts on_demand (interleaved)
    modes = ["pool", "on_demand"] * 3
    random.seed(42)
    # Balanced random order
    burst_plan = ["pool", "on_demand", "pool", "on_demand", "pool", "on_demand"]

    pool_rep = 0
    ondemand_rep = 0
    all_burst_results = []
    failed_submissions = 0
    total_submissions = 0

    required_fields = [
        "queue_delay", "mode", "workspace_creation_time",
        "execution_time", "workspace_cleanup_time", "total_worker_time"
    ]

    for idx, mode in enumerate(burst_plan):
        if mode == "pool":
            pool_rep += 1
            rep = pool_rep
        else:
            ondemand_rep += 1
            rep = ondemand_rep

        burst_id = f"gate_v5_{mode}_rep{rep}"
        print(f"\n[V5] Preparing Burst {idx+1}/{len(burst_plan)}: {burst_id} (mode={mode}, N={N})...")

        # Wait for steady state
        pm.wait_for_steady_state("patchwork-django:latest", target_buffer=B, max_containers=M, N=N, check_interval=2.0)

        sampler = BurstSampler(burst_id=burst_id)
        burst_res = await runner.run_burst(
            experiment="gate_v5",
            cell="W4_B4_M10_N10",
            burst_id=burst_id,
            mode=mode,
            workload=workload,
            N=N,
            W=W,
            B=B,
            M=M,
            repetition=rep,
            is_warmup=False,
            receiver=receiver,
            sampler=sampler
        )
        pm.record_burst_end()

        all_burst_results.append(burst_res)
        total_submissions += N
        failed_submissions += burst_res["count_failed"]

        # Verify sampler CSV exists and has samples
        sample_path = f"eval/results/raw/samples/{burst_id}.csv"
        if not os.path.exists(sample_path) or os.path.getsize(sample_path) == 0:
            print(f"[V5] Error: Sampler output missing or empty for {burst_id}")
            return False

    await receiver.stop()
    pm.stop_workers()
    pm.stop_daemon()

    # Verify all submissions succeeded
    all_succeeded = (failed_submissions == 0 and total_submissions == 60)
    print(f"\n[V5] Total submissions: {total_submissions}, Failures: {failed_submissions}")

    # Verify fields in raw submissions.csv
    import pandas as pd
    df = pd.read_csv("eval/results/raw/submissions.csv")
    v5_df = df[df["experiment"] == "gate_v5"]
    print(f"[V5] Submissions recorded in CSV: {len(v5_df)}")

    metadata_ok = True
    for f in required_fields:
        null_count = v5_df[f].isnull().sum()
        if null_count > 0:
            print(f"[V5] Missing field {f}: {null_count} nulls")
            metadata_ok = False

    passed = all_succeeded and metadata_ok and len(v5_df) == 60
    print(f"=== Gate V5 Result: {'PASS' if passed else 'FAIL'} ===")

    os.makedirs("eval/results/gates", exist_ok=True)
    with open("eval/results/gates/v5.json", "w", encoding="utf-8") as f:
        json.dump({
            "passed": passed,
            "total_submissions": total_submissions,
            "failed_submissions": failed_submissions,
            "metadata_ok": metadata_ok,
            "bursts": all_burst_results
        }, f, indent=2)

    return passed

if __name__ == "__main__":
    success = asyncio.run(run_v5())
    sys.exit(0 if success else 1)
