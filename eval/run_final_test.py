import asyncio
import os
import sys
import time
import json
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager
from burst_runner import BurstRunner
from sampler import BurstSampler
from generate_reports import generate_all_tables_and_figures
from build_report import build_full_report

async def run_final():
    print("===================================================================")
    print("      Patchwork-Service Final High-Capacity Test (W=8, B=8, M=15) ")
    print("===================================================================")

    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()
    runner = BurstRunner(api_url="http://127.0.0.1:8000")

    # Configure expanded pool size: M=15, B=8
    print("[Final Test] Configuring pool ceiling M=15 and target buffer B=8...")
    pm.set_pool_max_containers("patchwork-django", 15)
    pm.set_pool_max_containers("patchwork-express", 15)
    pm.start_daemon(target_buffer=8, max_pool_size=15, check_interval=1.5)
    await asyncio.sleep(4)

    # Start 8 workers
    print("[Final Test] Starting 8 workers (W=8)...")
    pm.start_workers(8, cell_name="final_W8_B8_M15")
    await asyncio.sleep(3)

    cell_name = "final_W8_B8_M15_N50"
    N = 50
    W = 8
    B = 8
    M = 15

    burst_id_pool = f"{cell_name}_pool_rep1"
    df_b = pd.read_csv("eval/results/raw/bursts.csv") if os.path.exists("eval/results/raw/bursts.csv") else pd.DataFrame()
    already_pool = not df_b.empty and burst_id_pool in df_b["burst_id"].values

    if not already_pool:
        print(f"\n[Final Test] Waiting for steady state (idle=8 containers)...")
        pm.wait_for_steady_state("patchwork-django:latest", target_buffer=8, max_containers=15, N=50, check_interval=1.5)

        print(f"\n[Final Test] Running Pool Burst (N={N}, W={W}, B={B}, M={M})...")
        sampler_pool = BurstSampler(burst_id=burst_id_pool)
        res_pool = await runner.run_burst(
            experiment="final",
            cell=cell_name,
            burst_id=burst_id_pool,
            mode="pool",
            workload="django",
            N=N,
            W=W,
            B=B,
            M=M,
            repetition=1,
            is_warmup=False,
            receiver=receiver,
            sampler=sampler_pool
        )
        pm.record_burst_end()
        print(f"[Final Test] Pool Burst Complete: duration={res_pool['burst_duration_s']:.2f}s, p50={res_pool['p50_e2e_s']:.2f}s, success={res_pool['count_success']}/{N}")
    else:
        print(f"[Final Test] Pool burst {burst_id_pool} already completed and recorded in bursts.csv.")

    # 2. On-Demand Mode Burst (N=50, W=8)
    print(f"\n[Final Test] Waiting for steady state before on-demand burst...")
    pm.wait_for_steady_state("patchwork-django:latest", target_buffer=8, max_containers=15, N=50, check_interval=1.5)

    print(f"\n[Final Test] Running On-Demand Burst (N={N}, W={W}, B={B}, M={M})...")
    burst_id_ondemand = f"{cell_name}_on_demand_rep1"
    sampler_ondemand = BurstSampler(burst_id=burst_id_ondemand)
    res_ondemand = await runner.run_burst(
        experiment="final",
        cell=cell_name,
        burst_id=burst_id_ondemand,
        mode="on_demand",
        workload="django",
        N=N,
        W=W,
        B=B,
        M=M,
        repetition=1,
        is_warmup=False,
        receiver=receiver,
        sampler=sampler_ondemand
    )
    pm.record_burst_end()
    print(f"[Final Test] On-Demand Burst Complete: duration={res_ondemand['burst_duration_s']:.2f}s, p50={res_ondemand['p50_e2e_s']:.2f}s, success={res_ondemand['count_success']}/{N}")

    # Stop processes
    print("\n[Final Test] Stopping workers, daemon, and receiver...")
    pm.stop_workers()
    pm.stop_daemon()
    await receiver.stop()

    # Regenerate reports
    print("[Final Test] Updating tables, figures, and REPORT.md...")
    generate_all_tables_and_figures()
    build_full_report()

    print("===================================================================")
    print("      Final Test & Report Generation Complete!                    ")
    print("===================================================================")

if __name__ == "__main__":
    asyncio.run(run_final())
