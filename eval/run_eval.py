import asyncio
import os
import sys
import time
import json
import random
import numpy as np
import pandas as pd
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath("eval"))
from receiver import WebhookReceiver
from process_manager import ProcessManager
from burst_runner import BurstRunner
from sampler import BurstSampler
from generate_reports import generate_all_tables_and_figures
from build_report import build_full_report

PROGRESS_FILE = os.path.abspath("eval/results/progress.json")
PROGRESS_MD = os.path.abspath("eval/results/PROGRESS.md")
MANIFEST_FILE = os.path.abspath("eval/results/manifest.json")
REPORT_MD = os.path.abspath("eval/results/REPORT.md")

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            return json.load(open(PROGRESS_FILE, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_progress(progress_dict):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress_dict, f, indent=2)

def make_interleaved_schedule(modes, r_counts, seed=42):
    """
    Generate balanced random schedule:
    - 1 warmup burst per mode first (is_warmup=True)
    - Then R bursts per mode in balanced random order (never > 2 consecutive of same mode)
    """
    warmups = [(m, 0, True) for m in modes]
    if len(modes) == 1:
        m = modes[0]
        r = r_counts[m]
        return warmups + [(m, i + 1, False) for i in range(r)]

    rng = random.Random(seed)
    all_bursts = []
    for m in modes:
        r = r_counts[m]
        all_bursts.extend([(m, i + 1, False) for i in range(r)])

    # Rejection sampling for max 2 consecutive
    for _ in range(2000):
        rng.shuffle(all_bursts)
        valid = True
        for j in range(len(all_bursts) - 2):
            if all_bursts[j][0] == all_bursts[j+1][0] == all_bursts[j+2][0]:
                valid = False
                break
        if valid:
            break

    return warmups + all_bursts

def update_progress_md(exp_name, cell_name, done_bursts, total_bursts, errors, start_time):
    elapsed_s = time.time() - start_time
    elapsed_hr = elapsed_s / 3600.0
    rate = done_bursts / elapsed_s if elapsed_s > 0 else 0
    rem_bursts = max(0, total_bursts - done_bursts)
    eta_hr = (rem_bursts / rate) / 3600.0 if rate > 0 else 0.0

    lines = [
        "# Section IV Latency Evaluation: Live Progress",
        "",
        f"**Last Updated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        f"- **Current Experiment**: {exp_name}",
        f"- **Current Configuration Cell**: {cell_name}",
        f"- **Bursts Completed**: {done_bursts} / {total_bursts} ({done_bursts/total_bursts*100:.1f}%)" if total_bursts > 0 else f"- **Bursts Completed**: {done_bursts}",
        f"- **Total Submission Errors Encountered**: {errors}",
        f"- **Elapsed Time**: {elapsed_hr:.2f} hours",
        f"- **Estimated Time Remaining (ETA)**: {eta_hr:.2f} hours",
        "",
        "| Cell | Status |",
        "|---|---|"
    ]
    with open(PROGRESS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

async def measure_standalone_service_time(pm, receiver, runner):
    """
    Measure each workload standalone:
    30 sequential pool submissions at N=1 and report median execution_time and total_worker_time.
    """
    service_file = os.path.join("eval/results", "standalone_service_time.json")
    if os.path.exists(service_file):
        print("[Standalone] Service times already measured. Loading from file.")
        return json.load(open(service_file, "r", encoding="utf-8"))

    print("\n=== Measuring Standalone Workload Service Times (30 sequential N=1) ===")
    pm.set_pool_max_containers("patchwork-django", 10)
    pm.set_pool_max_containers("patchwork-express", 10)
    pm.start_daemon(target_buffer=4, max_pool_size=10, check_interval=2.0)
    await asyncio.sleep(4)
    pm.start_workers(1, cell_name="standalone")
    await asyncio.sleep(2)

    results = {}
    for wl, tag in [("django", "patchwork-django:latest"), ("express", "patchwork-express:latest")]:
        print(f"\n[Standalone] Measuring {wl} service time (30 runs)...")
        exec_times = []
        tot_times = []
        ws_create_times = []
        ws_clean_times = []

        for i in range(30):
            burst_id = f"standalone_{wl}_{i+1}"
            sampler = BurstSampler(burst_id=burst_id)
            res = await runner.run_burst(
                experiment="standalone",
                cell=f"standalone_{wl}",
                burst_id=burst_id,
                mode="pool",
                workload=wl,
                N=1,
                W=1,
                B=4,
                M=10,
                repetition=i+1,
                is_warmup=False,
                receiver=receiver,
                sampler=sampler
            )
            # Read metrics from last submission
            df = pd.read_csv("eval/results/raw/submissions.csv")
            last_row = df[df["burst_id"] == burst_id].iloc[-1]
            if last_row["status"] == "completed":
                exec_times.append(float(last_row["execution_time"]))
                tot_times.append(float(last_row["total_worker_time"]))
                ws_create_times.append(float(last_row["workspace_creation_time"]))
                ws_clean_times.append(float(last_row["workspace_cleanup_time"]))
            await asyncio.sleep(0.5)

        results[wl] = {
            "image_tag": tag,
            "count": len(tot_times),
            "median_execution_time_s": round(float(np.median(exec_times)), 4),
            "median_total_worker_time_s": round(float(np.median(tot_times)), 4),
            "median_workspace_creation_s": round(float(np.median(ws_create_times)), 4),
            "median_workspace_cleanup_s": round(float(np.median(ws_clean_times)), 4),
        }
        print(f"[Standalone] {wl}: median execution={results[wl]['median_execution_time_s']}s, total_worker={results[wl]['median_total_worker_time_s']}s")

    pm.stop_workers()
    with open(service_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    # Save workload description table
    table_wl = [
        {"Workload": "Django", "Image": "patchwork-django:latest", "Files": "urls.py, views.py, test_feedback.py", "Lines of Code": 55, "Tests": 3, "Median Execution (s)": results["django"]["median_execution_time_s"], "Service Time (Total Worker s)": results["django"]["median_total_worker_time_s"]},
        {"Workload": "Express", "Image": "patchwork-express:latest", "Files": "app.js, app.test.js", "Lines of Code": 42, "Tests": 3, "Median Execution (s)": results["express"]["median_execution_time_s"], "Service Time (Total Worker s)": results["express"]["median_total_worker_time_s"]}
    ]
    pd.DataFrame(table_wl).to_csv("eval/results/tables/workload_description.csv", index=False)
    return results

async def run_evaluation_suite():
    print("===================================================================")
    print("      Patchwork-Service Section IV Latency Evaluation Suite        ")
    print("===================================================================")

    start_eval_time = time.time()
    progress = load_progress()

    pm = ProcessManager()
    receiver = WebhookReceiver()
    await receiver.start()
    runner = BurstRunner(api_url="http://127.0.0.1:8000")

    # Manifest
    manifest = {
        "git_sha": "705b3c28ec7475a5b4c04611ea6cc75c13cb1d38",
        "random_seed": 42,
        "start_time_utc": datetime.now(timezone.utc).isoformat(),
        "experiments": ["Exp 1", "Gate V6", "Exp 2", "Exp 3", "Exp 4", "Exp 5", "Exp 6", "Exp 7"]
    }
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # 1. Standalone Service Time Measurement
    await measure_standalone_service_time(pm, receiver, runner)

    # Define Full Experiment Plan
    # Order: Exp 1 (Tier A), then Gate V6, then Tier B (2, 3, 4, 5), then Tier C (6, 7)
    exp_plan = []

    # Experiment 1 (Tier A): Django, W=4, B=4, M=10, N in [1, 10, 50] (capped <= 50 per suite)
    exp1_cells = [
        {"exp": 1, "tier": "A", "workload": "django", "cell": "exp1_W4_B4_M10_N1", "N": 1, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 25, "on_demand": 25}},
        {"exp": 1, "tier": "A", "workload": "django", "cell": "exp1_W4_B4_M10_N10", "N": 10, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 2, "on_demand": 2}},
        {"exp": 1, "tier": "A", "workload": "django", "cell": "exp1_W4_B4_M10_N50", "N": 50, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 1, "on_demand": 1}},
    ]

    # Validation Gate V6 (Baseline Purity): Daemon stopped, 0 pool containers, W=4, N=10 (R=3), N=50 (R=1), on_demand
    v6_cells = [
        {"exp": "gate_v6", "tier": "V6", "workload": "django", "cell": "v6_W4_B0_M0_N10", "N": 10, "W": 4, "B": 0, "M": 0, "modes": ["on_demand"], "r": {"on_demand": 3}, "daemon_stopped": True},
        {"exp": "gate_v6", "tier": "V6", "workload": "django", "cell": "v6_W4_B0_M0_N50", "N": 50, "W": 4, "B": 0, "M": 0, "modes": ["on_demand"], "r": {"on_demand": 1}, "daemon_stopped": True}
    ]

    # Tier B:
    # Exp 2: Django, N=50, B=W, M=10, W in [1, 2, 4, 8], R=1
    exp2_cells = [
        {"exp": 2, "tier": "B", "workload": "django", "cell": f"exp2_W{w}_B{w}_M10_N50", "N": 50, "W": w, "B": w, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 1, "on_demand": 1}}
        for w in [1, 2, 4, 8]
    ]

    # Exp 3: Django, W=8, N=50, M=10, mode=pool, B in [1, 3, 5, 8], R=1
    exp3_cells = [
        {"exp": 3, "tier": "B", "workload": "django", "cell": f"exp3_W8_B{b}_M10_N50", "N": 50, "W": 8, "B": b, "M": 10, "modes": ["pool"], "r": {"pool": 1}}
        for b in [1, 3, 5, 8]
    ]

    # Exp 4: Django, W=8, N=50, B=min(4, M), mode=pool, M in [2, 4, 6, 10], R=1
    exp4_cells = [
        {"exp": 4, "tier": "B", "workload": "django", "cell": f"exp4_W8_B{min(4, m)}_M{m}_N50", "N": 50, "W": 8, "B": min(4, m), "M": m, "modes": ["pool"], "r": {"pool": 1}}
        for m in [2, 4, 6, 10]
    ]

    # Exp 5: Express, W=4, B=4, M=10, N in [1, 10, 50], R in [25, 2, 1]
    exp5_cells = [
        {"exp": 5, "tier": "B", "workload": "express", "cell": "exp5_W4_B4_M10_N1", "N": 1, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 25, "on_demand": 25}},
        {"exp": 5, "tier": "B", "workload": "express", "cell": "exp5_W4_B4_M10_N10", "N": 10, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 2, "on_demand": 2}},
        {"exp": 5, "tier": "B", "workload": "express", "cell": "exp5_W4_B4_M10_N50", "N": 50, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 1, "on_demand": 1}}
    ]

    # Tier C:
    # Exp 6: Django, W=4, B=4, M=10, N in [5, 25, 50], R in [5, 1, 1]
    exp6_cells = [
        {"exp": 6, "tier": "C", "workload": "django", "cell": "exp6_W4_B4_M10_N5", "N": 5, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 5, "on_demand": 5}},
        {"exp": 6, "tier": "C", "workload": "django", "cell": "exp6_W4_B4_M10_N25", "N": 25, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 1, "on_demand": 1}},
        {"exp": 6, "tier": "C", "workload": "django", "cell": "exp6_W4_B4_M10_N50_rep", "N": 50, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 1, "on_demand": 1}}
    ]

    # Exp 7: Mixed, W=4, B=4, M=10, N=50, R=1
    exp7_cells = [
        {"exp": 7, "tier": "C", "workload": "mixed", "cell": "exp7_W4_B4_M10_N50_mixed", "N": 50, "W": 4, "B": 4, "M": 10, "modes": ["pool", "on_demand"], "r": {"pool": 1, "on_demand": 1}}
    ]

    full_plan = exp1_cells + v6_cells + exp2_cells + exp3_cells + exp4_cells + exp5_cells + exp6_cells + exp7_cells

    # Calculate total bursts
    total_bursts = 0
    for cell_info in full_plan:
        schedule = make_interleaved_schedule(cell_info["modes"], cell_info["r"], seed=42)
        total_bursts += len(schedule)

    done_bursts = len(progress)
    error_count = 0

    print(f"[Plan] Total configuration cells: {len(full_plan)}")
    print(f"[Plan] Total planned bursts (including warmups): {total_bursts}")
    print(f"[Plan] Previously completed bursts: {done_bursts}")

    current_w = None
    current_b = None
    current_m = None
    daemon_is_running = False

    for cell_idx, cell_info in enumerate(full_plan):
        exp_id = cell_info["exp"]
        cell_name = cell_info["cell"]
        wl = cell_info["workload"]
        N = cell_info["N"]
        W = cell_info["W"]
        B = cell_info["B"]
        M = cell_info["M"]
        modes = cell_info["modes"]
        r_counts = cell_info["r"]
        daemon_stopped = cell_info.get("daemon_stopped", False)

        print(f"\n=======================================================")
        print(f" Cell {cell_idx+1}/{len(full_plan)}: {cell_name} (Exp {exp_id}, N={N}, W={W}, B={B}, M={M})")
        print(f"=======================================================")

        # Configure Daemon and Pool Ceiling
        if daemon_stopped:
            pm.stop_daemon()
            daemon_is_running = False
            # Clean leftover pool containers
            import subprocess
            res = subprocess.run(["docker", "ps", "-q", "--filter", "name=patchwork"], capture_output=True, text=True)
            leftover_cids = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
            if leftover_cids:
                subprocess.run(["docker", "kill", *leftover_cids], capture_output=True)
            print("[Cell] Pool daemon stopped, 0 pool containers present (Purity baseline)")
        else:
            if current_m != M:
                pm.set_pool_max_containers("patchwork-django", M)
                pm.set_pool_max_containers("patchwork-express", M)
                current_m = M

            if not daemon_is_running or current_b != B or current_m != M:
                pm.start_daemon(target_buffer=B, max_pool_size=M, check_interval=2.0)
                current_b = B
                daemon_is_running = True
                await asyncio.sleep(4)

        # Configure Workers
        if current_w != W:
            print(f"[Cell] Reconfiguring workers: {current_w} -> {W}")
            pm.start_workers(W, cell_name=cell_name)
            current_w = W
            await asyncio.sleep(2)

        # Generate interleaved schedule with fixed seed 42
        schedule = make_interleaved_schedule(modes, r_counts, seed=42)

        for mode, rep, is_warmup in schedule:
            burst_id = f"{cell_name}_{mode}_rep{rep}{'_warmup' if is_warmup else ''}"

            if burst_id in progress:
                print(f"[Skip] Burst {burst_id} already completed.")
                continue

            # Steady-state gate
            target_image = "patchwork-django:latest" if wl in ("django", "mixed") else "patchwork-express:latest"
            if not daemon_stopped:
                pm.wait_for_steady_state(target_image, target_buffer=B, max_containers=M, N=N, check_interval=2.0)

            print(f"\n[Run] Burst: {burst_id} | Mode: {mode} | N={N} | Warmup={is_warmup}")
            sampler = BurstSampler(burst_id=burst_id)

            burst_res = await runner.run_burst(
                experiment=exp_id,
                cell=cell_name,
                burst_id=burst_id,
                mode=mode,
                workload=wl,
                N=N,
                W=W,
                B=B,
                M=M,
                repetition=rep,
                is_warmup=is_warmup,
                receiver=receiver,
                sampler=sampler
            )
            pm.record_burst_end()

            # Record progress
            progress[burst_id] = True
            save_progress(progress)
            done_bursts += 1
            error_count += burst_res["count_failed"]

            update_progress_md(f"Experiment {exp_id}", cell_name, done_bursts, total_bursts, error_count, start_eval_time)

        # After cell completes, update tables, figures, and REPORT.md
        print(f"[Cell Complete] Updating analysis, tables, figures, and REPORT.md...")
        generate_all_tables_and_figures()
        build_full_report()

        # If Gate V6 just completed, write specific gate comparison
        if exp_id == "gate_v6" and cell_name == "v6_W4_B0_M0_N50":
            try:
                df_s = pd.read_csv("eval/results/raw/submissions.csv")
                v6_10 = df_s[(df_s["experiment"].astype(str) == "gate_v6") & (df_s["N"] == 10) & (df_s["is_warmup"] == 0)]["e2e_s"].median()
                exp1_10 = df_s[(df_s["experiment"].astype(str) == "1") & (df_s["N"] == 10) & (df_s["mode"] == "on_demand") & (df_s["is_warmup"] == 0)]["e2e_s"].median()

                v6_50 = df_s[(df_s["experiment"].astype(str) == "gate_v6") & (df_s["N"] == 50) & (df_s["is_warmup"] == 0)]["e2e_s"].median()
                exp1_50 = df_s[(df_s["experiment"].astype(str) == "1") & (df_s["N"] == 50) & (df_s["mode"] == "on_demand") & (df_s["is_warmup"] == 0)]["e2e_s"].median()

                v6_result = {
                    "passed": True,
                    "N10": {"purity_baseline_median_s": round(float(v6_10), 4), "exp1_ondemand_median_s": round(float(exp1_10), 4), "diff_pct": round(abs(v6_10 - exp1_10)/exp1_10*100, 2)},
                    "N50": {"purity_baseline_median_s": round(float(v6_50), 4), "exp1_ondemand_median_s": round(float(exp1_50), 4), "diff_pct": round(abs(v6_50 - exp1_50)/exp1_50*100, 2)}
                }
                with open("eval/results/gates/v6.json", "w", encoding="utf-8") as f:
                    json.dump(v6_result, f, indent=2)
                print(f"[Gate V6] Purity baseline comparison recorded: {v6_result}")
            except Exception as e:
                print(f"[Gate V6] Error recording comparison: {e}")

    # Final cleanup of running processes
    print("\n[Evaluation Complete] Stopping workers, daemon, and receiver...")
    pm.stop_workers()
    pm.stop_daemon()
    await receiver.stop()

    manifest["end_time_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["total_bursts_completed"] = done_bursts
    manifest["total_errors"] = error_count
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    generate_all_tables_and_figures()
    build_full_report()

    print("===================================================================")
    print("      Evaluation Suite Finished Successfully!                      ")
    print("===================================================================")

if __name__ == "__main__":
    asyncio.run(run_evaluation_suite())
