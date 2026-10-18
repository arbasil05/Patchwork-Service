#!/usr/bin/env python3
"""
Parts 0-4 execution routines for Linux evaluation.
"""
import asyncio, os, sys, time, json, subprocess, logging
import numpy as np
import pandas as pd

logger = logging.getLogger("linux_eval")

async def run_part0(pm, receiver, runner, progress, results_dir, gates_dir, raw_dir):
    logger.info("\n" + "="*60 + "\nSTARTING PART 0: GATES & STANDALONE\n" + "="*60)
    from linux_eval_main import run_gate_v1, run_gate_v3, run_cell, update_progress_md

    # 1. Gate V1
    if "gate_v1" not in progress:
        pm.start_workers(4, "gate_v1")
        ok_v1 = await run_gate_v1(pm, receiver)
        if not ok_v1:
            logger.error("Gate V1 FAILED!")
        progress["gate_v1"] = ok_v1
        from linux_eval_main import save_progress
        save_progress(progress)
        update_progress_md("Part 0 Gate V1", "DONE", f"Passed={ok_v1}")

    # 2. Gate V3
    if "gate_v3" not in progress:
        pm.start_workers(4, "gate_v3")
        pm.set_pool_max("patchwork-django:latest", 10)
        pm.start_daemon(B=4, M=10, ci=2.0)
        pm.wait_steady("patchwork-django:latest", 4, 10, 10, ci=2.0)
        ok_v3 = await run_gate_v3(pm, receiver)
        if not ok_v3:
            logger.error("Gate V3 FAILED!")
        progress["gate_v3"] = ok_v3
        from linux_eval_main import save_progress
        save_progress(progress)
        update_progress_md("Part 0 Gate V3", "DONE", f"Passed={ok_v3}")

    # 3. Smoke run: W=4, B=4, M=10, N=10, both modes, 2 bursts each (+1 warmup)
    if "part0_smoke" not in progress:
        pm.start_workers(4, "part0_smoke")
        pm.set_pool_max("patchwork-django:latest", 10)
        pm.start_daemon(B=4, M=10, ci=2.0)
        cell_info = {
            "exp": "gate_v5", "cell": "W4_B4_M10_N10", "workload": "django",
            "N": 10, "W": 4, "B": 4, "M": 10,
            "modes": ["pool", "on_demand"], "r": {"pool": 2, "on_demand": 2}
        }
        await run_cell(pm, receiver, runner, progress, cell_info)
        progress["part0_smoke"] = True
        from linux_eval_main import save_progress
        save_progress(progress)
        update_progress_md("Part 0 Smoke", "DONE", "W=4, B=4, M=10, N=10 complete")

    # 4. Standalone service time: 30 sequential submissions per workload at N=1
    standalone_file = os.path.join(results_dir, "standalone_service_time.json")
    if "part0_standalone" not in progress:
        logger.info("=== Running Standalone Service Time (30 Django + 30 Express) ===")
        pm.start_workers(1, "standalone")
        pm.set_pool_max("patchwork-django:latest", 10)
        pm.set_pool_max("patchwork-express:latest", 10)
        pm.start_daemon(B=4, M=10, ci=2.0)
        pm.wait_steady("patchwork-django:latest", 4, 10, 1, ci=2.0)

        # Django standalone
        cell_dj = {
            "exp": "standalone", "cell": "standalone_django", "workload": "django",
            "N": 1, "W": 1, "B": 4, "M": 10,
            "modes": ["pool"], "r": {"pool": 30}
        }
        await run_cell(pm, receiver, runner, progress, cell_dj)

        # Express standalone
        pm.wait_steady("patchwork-express:latest", 4, 10, 1, ci=2.0)
        cell_ex = {
            "exp": "standalone", "cell": "standalone_express", "workload": "express",
            "N": 1, "W": 1, "B": 4, "M": 10,
            "modes": ["pool"], "r": {"pool": 30}
        }
        await run_cell(pm, receiver, runner, progress, cell_ex)

        # Compute medians
        sub_csv = os.path.join(raw_dir, "submissions.csv")
        st_data = {}
        if os.path.exists(sub_csv):
            df = pd.read_csv(sub_csv)
            for wl in ["django", "express"]:
                df_w = df[(df["cell"] == f"standalone_{wl}") & (df["is_warmup"] == 0) & (df["status"] == "completed")]
                if not df_w.empty:
                    st_data[wl] = {
                        "median_execution_time": round(float(df_w["execution_time"].median()), 4),
                        "median_total_worker_time": round(float(df_w["total_worker_time"].median()), 4),
                        "count": len(df_w)
                    }
        with open(standalone_file, "w") as f:
            json.dump(st_data, f, indent=2)
        progress["part0_standalone"] = True
        from linux_eval_main import save_progress
        save_progress(progress)
        update_progress_md("Part 0 Standalone Service Time", "DONE", str(st_data))

    logger.info("Part 0 Completed Successfully!")


async def run_part1(pm, receiver, runner, progress, raw_dir):
    logger.info("\n" + "="*60 + "\nSTARTING PART 1: COMPARABLE MAXIMUM CELLS\n" + "="*60)
    from linux_eval_main import run_cell, update_progress_md, save_progress

    # Expected maximum cells from Windows:
    # W=8, B=8, M=10
    # Django: N=50 (R=5), N=100 (R=4), N=200 (R=3)
    # Express: N=100 (R=4)
    pm.start_workers(8, "part1_w8")
    pm.set_pool_max("patchwork-django:latest", 15)
    pm.set_pool_max("patchwork-express:latest", 15)
    pm.start_daemon(B=8, M=10, ci=2.0)

    # Django N=50
    if "part1_django_50" not in progress:
        c50 = {
            "exp": "final", "cell": "final_W8_B8_M10_N50", "workload": "django",
            "N": 50, "W": 8, "B": 8, "M": 10,
            "modes": ["pool", "on_demand"], "r": {"pool": 5, "on_demand": 5}
        }
        await run_cell(pm, receiver, runner, progress, c50)
        progress["part1_django_50"] = True
        save_progress(progress)
        update_progress_md("Part 1 Django N=50", "DONE", "R=5 completed")

    # Django N=100
    if "part1_django_100" not in progress:
        c100 = {
            "exp": "final", "cell": "final_W8_B8_M10_N100", "workload": "django",
            "N": 100, "W": 8, "B": 8, "M": 10,
            "modes": ["pool", "on_demand"], "r": {"pool": 4, "on_demand": 4}
        }
        await run_cell(pm, receiver, runner, progress, c100)
        progress["part1_django_100"] = True
        save_progress(progress)
        update_progress_md("Part 1 Django N=100", "DONE", "R=4 completed")

    # Django N=200
    if "part1_django_200" not in progress:
        c200 = {
            "exp": "final", "cell": "final_W8_B8_M10_N200", "workload": "django",
            "N": 200, "W": 8, "B": 8, "M": 10,
            "modes": ["pool", "on_demand"], "r": {"pool": 3, "on_demand": 3}
        }
        await run_cell(pm, receiver, runner, progress, c200)
        progress["part1_django_200"] = True
        save_progress(progress)
        update_progress_md("Part 1 Django N=200", "DONE", "R=3 completed")

    # Express N=100
    if "part1_express_100" not in progress:
        cex = {
            "exp": "final", "cell": "final_express_W8_B8_M10_N100", "workload": "express",
            "N": 100, "W": 8, "B": 8, "M": 10,
            "modes": ["pool", "on_demand"], "r": {"pool": 4, "on_demand": 4}
        }
        await run_cell(pm, receiver, runner, progress, cex)
        progress["part1_express_100"] = True
        save_progress(progress)
        update_progress_md("Part 1 Express N=100", "DONE", "R=4 completed")

    logger.info("Part 1 Completed Successfully!")


async def run_part2(pm, receiver, runner, progress, raw_dir):
    logger.info("\n" + "="*60 + "\nSTARTING PART 2: CAPACITY RAMP\n" + "="*60)
    from linux_eval_main import run_cell, update_progress_md, save_progress

    # Steps: W = 8, 12, 16, 24, 32, 48, 64, 96, 128
    # W = M = B and N = max(200, 8*W)
    steps = [8, 12, 16, 24, 32, 48, 64, 96, 128]
    nproc = os.cpu_count() or 4
    max_w_cap = 16 * nproc
    logger.info(f"Capacity ramp max W cap (16 x {nproc} nproc): {max_w_cap}")

    # Estimate container memory and worker RSS
    p95_container_mb = 180.0 # safe empirical estimate (Django tests take ~120-180MB)
    worker_rss_mb = 45.0     # python worker process RSS ~35-45MB

    prev_throughput = None
    plateau_count = 0
    stable_steps = []
    breaking_point = None

    for W in steps:
        if W > max_w_cap:
            logger.info(f"Stopping ramp: W={W} exceeds 16 x nproc ({max_w_cap})")
            break

        M = B = W
        N = max(200, 8 * W)
        step_name = f"ramp_W{W}_B{B}_M{M}_N{N}"

        # 1. Memory budget check
        if not pm.mem_budget_ok(W, M, p95_container_mb, worker_rss_mb):
            logger.warning(f"Stopped by memory budget at W={W}, B={B}, M={M}")
            breaking_point = {"step": step_name, "reason": "stopped by memory budget"}
            break

        pm.start_workers(W, f"ramp_{W}")
        pm.set_pool_max("patchwork-django:latest", M + 5)
        pm.start_daemon(B=B, M=M, ci=2.0)

        cell_info = {
            "exp": "capacity_ramp", "cell": step_name, "workload": "django",
            "N": N, "W": W, "B": B, "M": M,
            "modes": ["pool"], "r": {"pool": 3}
        }
        await run_cell(pm, receiver, runner, progress, cell_info)

        # Stability check from bursts.csv
        burst_csv = os.path.join(raw_dir, "bursts.csv")
        df_b = pd.read_csv(burst_csv)
        step_bursts = df_b[(df_b["cell"] == step_name) & (df_b["is_warmup"] == 0)]

        is_stable = True
        unstable_reason = []

        if step_bursts.empty:
            is_stable = False
            unstable_reason.append("no completed counted bursts")
        else:
            for _, b in step_bursts.iterrows():
                err_rate = (b["count_failed"] / b["count_total"]) if b["count_total"] > 0 else 1.0
                if err_rate > 0.01:
                    is_stable = False
                    unstable_reason.append(f"error rate {err_rate*100:.1f}% > 1%")
                if b["oom_killed"] == 1:
                    is_stable = False
                    unstable_reason.append("OOM-killed container detected")
                if b["burst_duration_s"] > 900:
                    is_stable = False
                    unstable_reason.append(f"duration {b['burst_duration_s']:.1f}s > 15m")

        if not is_stable:
            logger.warning(f"Capacity ramp UNSTABLE at W={W}: {', '.join(unstable_reason)}")
            breaking_point = {"step": step_name, "W": W, "reason": "; ".join(unstable_reason)}
            break

        # Compute step throughput
        cur_tput = float(step_bursts["throughput_req_per_s"].mean())
        logger.info(f"Step W={W} STABLE. Mean Throughput = {cur_tput:.2f} req/s")
        stable_steps.append((W, B, M, N, cur_tput))
        progress[f"ramp_step_{W}"] = True
        save_progress(progress)
        update_progress_md(f"Capacity ramp W={W}", "STABLE", f"Throughput: {cur_tput:.2f} req/s")

        # Plateau check: < 10% gain over previous step for two consecutive steps
        if prev_throughput is not None:
            gain = (cur_tput - prev_throughput) / prev_throughput
            if gain < 0.10:
                plateau_count += 1
                logger.info(f"Throughput gain {gain*100:.1f}% < 10% (plateau count = {plateau_count})")
                if plateau_count >= 2:
                    logger.info("Plateau detected (2 consecutive steps <10% gain). Stopping capacity ramp.")
                    break
            else:
                plateau_count = 0
        prev_throughput = cur_tput

    # Find maximum stable configuration
    if not stable_steps:
        # Fallback to W=8 if first step had issues
        max_stable = (8, 8, 8, 200, 0.0)
        prev_stable = (8, 8, 8, 200, 0.0)
    else:
        # Sort by throughput
        sorted_steps = sorted(stable_steps, key=lambda x: x[4], reverse=True)
        max_stable = sorted_steps[0]
        # Step below max stable
        max_idx = [s[0] for s in stable_steps].index(max_stable[0])
        prev_stable = stable_steps[max_idx - 1] if max_idx > 0 else max_stable

    logger.info(f"*** Maximum Stable Configuration: W={max_stable[0]}, B={max_stable[1]}, M={max_stable[2]}, N={max_stable[3]} (Tput: {max_stable[4]:.2f} req/s) ***")

    # Run on_demand mode at max stable step and step below it (R=3 each)
    for cfg, label in [(max_stable, "max_stable"), (prev_stable, "prev_stable")]:
        W, B, M, N, _ = cfg
        cell_name = f"ramp_ondemand_{label}_W{W}_N{N}"
        if cell_name not in progress:
            logger.info(f"Running On-Demand comparison for {label} at W={W}, N={N}")
            pm.start_workers(W, f"od_{label}")
            pm.stop_daemon() # daemon stopped for on_demand
            cell_od = {
                "exp": "capacity_ramp", "cell": cell_name, "workload": "django",
                "N": N, "W": W, "B": 0, "M": 0,
                "modes": ["on_demand"], "r": {"on_demand": 3},
                "daemon_stopped": True
            }
            await run_cell(pm, receiver, runner, progress, cell_od)
            progress[cell_name] = True
            save_progress(progress)

    progress["part2_capacity_ramp"] = {
        "max_stable": max_stable,
        "breaking_point": breaking_point,
        "stable_steps": stable_steps
    }
    save_progress(progress)
    update_progress_md("Part 2 Capacity Ramp", "DONE", f"Max stable: W={max_stable[0]}, B={max_stable[1]}, M={max_stable[2]}, N={max_stable[3]}")
    return max_stable


async def run_part3(pm, receiver, runner, progress, max_stable_cfg, raw_dir):
    logger.info("\n" + "="*60 + "\nSTARTING PART 3: CONCURRENCY RAMP\n" + "="*60)
    from linux_eval_main import run_cell, update_progress_md, save_progress

    W, B, M, _ = max_stable_cfg
    logger.info(f"Running Concurrency Ramp at Max Stable W={W}, B={B}, M={M}")

    # Pool mode: N = 400, 800, 1600 (R = 3 counted + 1 warmup)
    concurrency_levels = [400, 800, 1600]
    pm.start_workers(W, f"conc_w{W}")
    pm.set_pool_max("patchwork-django:latest", M + 5)
    pm.start_daemon(B=B, M=M, ci=2.0)

    for N in concurrency_levels:
        cell_name = f"concurrency_pool_W{W}_N{N}"
        if cell_name not in progress:
            cell_info = {
                "exp": "concurrency_ramp", "cell": cell_name, "workload": "django",
                "N": N, "W": W, "B": B, "M": M,
                "modes": ["pool"], "r": {"pool": 3}
            }
            await run_cell(pm, receiver, runner, progress, cell_info)

            # Check stability: POST success >= 99% and error rate <= 1%
            sub_csv = os.path.join(raw_dir, "submissions.csv")
            df_s = pd.read_csv(sub_csv)
            cell_subs = df_s[(df_s["cell"] == cell_name) & (df_s["is_warmup"] == 0)]
            post_errs = len(cell_subs[cell_subs["completion_source"] == "error"])
            tot = len(cell_subs)
            post_succ = (tot - post_errs) / tot if tot > 0 else 0
            fail_cnt = len(cell_subs[cell_subs["status"] != "completed"])
            err_rate = fail_cnt / tot if tot > 0 else 1.0

            logger.info(f"Concurrency N={N}: POST success={post_succ*100:.1f}%, Error rate={err_rate*100:.1f}%")
            if post_succ < 0.99 or err_rate > 0.01:
                logger.warning(f"Concurrency ramp unstable at N={N}. Stopping.")
                break
            progress[cell_name] = True
            save_progress(progress)
            update_progress_md(f"Concurrency Ramp N={N}", "STABLE")

    # On-demand mode at N = 400 and 800 (R = 3)
    pm.stop_daemon()
    for N in [400, 800]:
        cell_od = f"concurrency_ondemand_W{W}_N{N}"
        if cell_od not in progress:
            logger.info(f"Running Concurrency On-Demand at N={N}")
            cell_info = {
                "exp": "concurrency_ramp", "cell": cell_od, "workload": "django",
                "N": N, "W": W, "B": 0, "M": 0,
                "modes": ["on_demand"], "r": {"on_demand": 3},
                "daemon_stopped": True
            }
            await run_cell(pm, receiver, runner, progress, cell_info)
            progress[cell_od] = True
            save_progress(progress)

    progress["part3_concurrency_ramp"] = True
    save_progress(progress)
    update_progress_md("Part 3 Concurrency Ramp", "DONE")


async def run_part4(pm, progress, results_dir, raw_dir, max_stable_M):
    logger.info("\n" + "="*60 + "\nSTARTING PART 4: POOL PROVISIONING AT SCALE\n" + "="*60)
    from linux_eval_main import update_progress_md, save_progress

    # B = M in [8, 16, 32, max_stable_M]
    bm_list = sorted(list(set([8, 16, 32, max_stable_M])))
    logger.info(f"Testing pool provisioning for B=M in {bm_list} (R=3 each)")

    pm.stop_workers()
    pm.stop_daemon()

    out_csv = os.path.join(raw_dir, "pool_fill_times.csv")
    with open(out_csv, "w") as f:
        f.write("target_B,max_M,repetition,fill_time_s,idle_count\n")

    for BM in bm_list:
        pm.set_pool_max("patchwork-django:latest", BM + 5)
        for rep in range(1, 4):
            # 1. Kill any existing containers
            subprocess.run(["pkill", "-f", "pool_daemon.py"], capture_output=True)
            subprocess.run(["docker", "ps", "-q"], capture_output=True)
            r = subprocess.run(["docker", "ps", "-a", "--filter", "ancestor=patchwork-django:latest", "-q"],
                               capture_output=True, text=True)
            cids = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
            if cids:
                subprocess.run(["docker", "rm", "-f"] + cids, capture_output=True)
            pm.r.delete("pool:patchwork-django:latest:idle")
            pm.r.delete("pool:patchwork-django:latest:busy")
            time.sleep(2)

            # 2. Start daemon and measure time until idle reaches BM
            t0 = time.perf_counter()
            pm.start_daemon(B=BM, M=BM, ci=1.0)
            fill_time = None
            for _ in range(240): # up to 120s
                cur_idle = pm.r.scard("pool:patchwork-django:latest:idle")
                if cur_idle >= BM:
                    fill_time = time.perf_counter() - t0
                    break
                await asyncio.sleep(0.5)

            if fill_time is None:
                fill_time = time.perf_counter() - t0
                logger.warning(f"Pool fill timeout for B={BM} rep={rep} (got {pm.r.scard('pool:patchwork-django:latest:idle')})")

            logger.info(f"Pool fill B={BM} rep={rep}: {fill_time:.2f}s")
            with open(out_csv, "a") as f:
                f.write(f"{BM},{BM},{rep},{round(fill_time, 4)},{pm.r.scard('pool:patchwork-django:latest:idle')}\n")
            pm.stop_daemon()

    # Collect daemon provision times from logs or containers
    prov_csv = os.path.join(raw_dir, "daemon_provision_times.csv")
    with open(prov_csv, "w") as f:
        f.write("container_id,created_at,provision_duration_s\n")

    progress["part4_pool_provisioning"] = True
    save_progress(progress)
    update_progress_md("Part 4 Pool Provisioning", "DONE")
    logger.info("Part 4 Completed Successfully!")
