import os
import sys
import json
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

RESULTS_DIR = os.path.abspath("eval/results")
RAW_DIR = os.path.join(RESULTS_DIR, "raw")
TABLES_DIR = os.path.join(RESULTS_DIR, "tables")
FIGURES_DIR = os.path.join(RESULTS_DIR, "figures")

os.makedirs(TABLES_DIR, exist_ok=True)
os.makedirs(FIGURES_DIR, exist_ok=True)

def compute_bootstrap_ci(data_num, data_denom, n_resamples=2000, seed=42):
    """
    Bootstrap 95% CI for ratio of medians and ratio of P95s by resampling bursts.
    data_num: list of arrays/lists of e2e_s per burst for numerator (e.g. on_demand)
    data_denom: list of arrays/lists of e2e_s per burst for denominator (e.g. pool)
    """
    if not data_num or not data_denom:
        return (None, None), (None, None)

    rng = np.random.RandomState(seed)
    ratio_medians = []
    ratio_p95s = []

    k_num = len(data_num)
    k_denom = len(data_denom)

    for _ in range(n_resamples):
        sample_num_bursts = [data_num[i] for i in rng.choice(k_num, size=k_num, replace=True)]
        sample_denom_bursts = [data_denom[i] for i in rng.choice(k_denom, size=k_denom, replace=True)]

        flat_num = [x for b in sample_num_bursts for x in b]
        flat_denom = [x for b in sample_denom_bursts for x in b]

        if not flat_num or not flat_denom:
            continue

        med_num = np.median(flat_num)
        med_denom = np.median(flat_denom)
        p95_num = np.percentile(flat_num, 95)
        p95_denom = np.percentile(flat_denom, 95)

        if med_denom > 0:
            ratio_medians.append(med_num / med_denom)
        if p95_denom > 0:
            ratio_p95s.append(p95_num / p95_denom)

    ci_med = (round(float(np.percentile(ratio_medians, 2.5)), 3), round(float(np.percentile(ratio_medians, 97.5)), 3)) if ratio_medians else (None, None)
    ci_p95 = (round(float(np.percentile(ratio_p95s, 2.5)), 3), round(float(np.percentile(ratio_p95s, 97.5)), 3)) if ratio_p95s else (None, None)
    return ci_med, ci_p95

def df_to_latex(df, float_format="%.3f"):
    if df.empty:
        return ""
    cols = list(df.columns)
    col_align = "l" * len(cols)
    lines = [
        f"\\begin{{tabular}}{{{col_align}}}",
        "\\toprule",
        " & ".join(str(c) for c in cols) + " \\\\",
        "\\midrule"
    ]
    for _, row in df.iterrows():
        row_vals = []
        for v in row:
            if isinstance(v, (float, np.floating)):
                row_vals.append(float_format % v)
            else:
                row_vals.append(str(v))
        lines.append(" & ".join(row_vals) + " \\\\")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    return "\n".join(lines) + "\n"

def generate_all_tables_and_figures():
    sub_csv = os.path.join(RAW_DIR, "submissions.csv")
    burst_csv = os.path.join(RAW_DIR, "bursts.csv")

    if not os.path.exists(sub_csv) or not os.path.exists(burst_csv):
        print("[Analysis] No data CSVs found yet.")
        return

    df_sub = pd.read_csv(sub_csv)
    df_burst = pd.read_csv(burst_csv)

    df_sub["experiment"] = df_sub["experiment"].astype(str)
    df_burst["experiment"] = df_burst["experiment"].astype(str)

    # Exclude warmups for main evaluation statistics
    df_sub_valid = df_sub[df_sub["is_warmup"] == 0].copy()
    df_burst_valid = df_burst[df_burst["is_warmup"] == 0].copy()

    # Successful submissions for latency statistics
    df_success = df_sub_valid[(df_sub_valid["status"] == "completed") & (df_sub_valid["correct"] == 1)].copy()

    # 1. Table II: Environment & Worker Configurations
    env_file = os.path.join(RESULTS_DIR, "environment.json")
    env_data = json.load(open(env_file)) if os.path.exists(env_file) else {}
    table_ii = [
        {"Parameter": "Host CPU", "Value": env_data.get("cpu", {}).get("model", "i5-1235U")},
        {"Parameter": "Physical Cores / Logical Threads", "Value": f"{env_data.get('cpu', {}).get('physical_cores', 10)} / {env_data.get('cpu', {}).get('logical_processors', 12)}"},
        {"Parameter": "Host RAM", "Value": f"{env_data.get('ram', {}).get('total_gb', 16)} GB"},
        {"Parameter": "Operating System", "Value": f"{env_data.get('os', {}).get('caption', 'Windows 11')} (Build {env_data.get('os', {}).get('build', '26200')})"},
        {"Parameter": "Windows Power Plan", "Value": env_data.get("power", {}).get("plan", "Balanced")},
        {"Parameter": "Docker Desktop / Engine", "Value": f"v{env_data.get('docker', {}).get('desktop_version', '4.81.0')} / Engine {env_data.get('docker', {}).get('engine_version', '29.6.1')}"},
        {"Parameter": "Docker Assigned CPUs / Memory", "Value": f"{env_data.get('docker', {}).get('cpus', 12)} CPUs / {round(env_data.get('docker', {}).get('memory_bytes', 0)/(1024**3), 1)} GB"},
        {"Parameter": "WSL Configuration (.wslconfig)", "Value": env_data.get("docker", {}).get("wslconfig", "not found")},
        {"Parameter": "Redis Server Version / Image", "Value": f"{env_data.get('redis', {}).get('server_version', '8.8.0')} ({env_data.get('redis', {}).get('image', 'redis:latest')})"},
        {"Parameter": "Host Python / redis-py / rq", "Value": f"Python {env_data.get('python_environment', {}).get('host_python', '3.13.3')}, redis {env_data.get('python_environment', {}).get('redis_py', '8.0.1')}, rq {env_data.get('python_environment', {}).get('rq', '2.10.0')}"},
        {"Parameter": "patchwork-django:latest", "Value": f"394 MB, Python 3.12.11 (pytest)"},
        {"Parameter": "patchwork-express:latest", "Value": f"383 MB, Node v22.23.1 (Jest)"},
        {"Parameter": "Worker Configurations Evaluated", "Value": "W = 1, 2, 4, 8"},
        {"Parameter": "Git SHA", "Value": env_data.get("git_sha", "705b3c2")}
    ]
    pd.DataFrame(table_ii).to_csv(os.path.join(TABLES_DIR, "table_ii_environment.csv"), index=False)

    # 2. Table III: Main Latency Table (Django and Express)
    table_iii_rows = []
    speedup_rows = []

    # Filter experiments 1, 5, 6, final
    target_exps = ["1", "5", "6", "final"]
    exp_subset = df_sub_valid[df_sub_valid["experiment"].isin(target_exps)]

    cells = exp_subset.groupby(["experiment", "workload", "cell", "N", "W", "B", "M"]).size().reset_index()

    for _, c_row in cells.iterrows():
        exp = c_row["experiment"]
        wl = c_row["workload"]
        cell_name = c_row["cell"]
        N = c_row["N"]
        W = c_row["W"]
        B = c_row["B"]
        M = c_row["M"]

        for mode in ["pool", "on_demand"]:
            sub_m = df_sub_valid[
                (df_sub_valid["experiment"] == exp) &
                (df_sub_valid["cell"] == cell_name) &
                (df_sub_valid["mode"] == mode)
            ]
            if len(sub_m) == 0:
                continue

            n_total = len(sub_m)
            succ = sub_m[(sub_m["status"] == "completed") & (sub_m["correct"] == 1)]
            n_succ = len(succ)
            err_rate = round((n_total - n_succ) / n_total, 4)

            e2e = succ["e2e_s"].values
            med_e2e = round(float(np.median(e2e)), 4) if len(e2e) > 0 else None
            p95_e2e = round(float(np.percentile(e2e, 95)), 4) if len(e2e) > 0 else None
            p99_e2e = round(float(np.percentile(e2e, 99)), 4) if len(e2e) > 0 else None
            mean_e2e = round(float(np.mean(e2e)), 4) if len(e2e) > 0 else None

            # Per burst spread
            burst_medians = [np.median(succ[succ["burst_id"] == bid]["e2e_s"]) for bid in succ["burst_id"].unique() if len(succ[succ["burst_id"] == bid]) > 0]
            spread_min = round(float(min(burst_medians)), 4) if burst_medians else None
            spread_med = round(float(np.median(burst_medians)), 4) if burst_medians else None
            spread_max = round(float(max(burst_medians)), 4) if burst_medians else None

            hit_rate = None
            if mode == "pool":
                hits = succ["pool_hit"].dropna()
                hit_rate = round(float(hits.sum() / len(hits)), 4) if len(hits) > 0 else None

            # Stage medians
            ws_create = round(float(np.median(succ["workspace_creation_time"].dropna())), 4) if len(succ["workspace_creation_time"].dropna()) > 0 else None
            exec_time = round(float(np.median(succ["execution_time"].dropna())), 4) if len(succ["execution_time"].dropna()) > 0 else None
            ws_clean = round(float(np.median(succ["workspace_cleanup_time"].dropna())), 4) if len(succ["workspace_cleanup_time"].dropna()) > 0 else None
            tot_work = round(float(np.median(succ["total_worker_time"].dropna())), 4) if len(succ["total_worker_time"].dropna()) > 0 else None
            q_delay = round(float(np.median(succ["queue_delay"].dropna())), 4) if len(succ["queue_delay"].dropna()) > 0 else None

            table_iii_rows.append({
                "Experiment": exp,
                "Workload": wl,
                "Cell": cell_name,
                "N": N,
                "W": W,
                "B": B,
                "M": M,
                "Mode": mode,
                "Total (n)": n_total,
                "Success (n)": n_succ,
                "Median (s)": med_e2e,
                "Mean (s)": mean_e2e,
                "P95 (s)": p95_e2e,
                "P99 (s)": p99_e2e,
                "Error Rate": err_rate,
                "Hit Rate": hit_rate,
                "Spread Min": spread_min,
                "Spread Med": spread_med,
                "Spread Max": spread_max,
                "Queue Delay (s)": q_delay,
                "Workspace Create (s)": ws_create,
                "Execution Time (s)": exec_time,
                "Workspace Clean (s)": ws_clean,
                "Total Worker Time (s)": tot_work
            })

        # Speedup calculation for cell
        sub_pool = df_sub_valid[(df_sub_valid["cell"] == cell_name) & (df_sub_valid["mode"] == "pool") & (df_sub_valid["status"] == "completed")]
        sub_ondemand = df_sub_valid[(df_sub_valid["cell"] == cell_name) & (df_sub_valid["mode"] == "on_demand") & (df_sub_valid["status"] == "completed")]

        if len(sub_pool) > 0 and len(sub_ondemand) > 0:
            med_p = np.median(sub_pool["e2e_s"])
            med_o = np.median(sub_ondemand["e2e_s"])
            p95_p = np.percentile(sub_pool["e2e_s"], 95)
            p95_o = np.percentile(sub_ondemand["e2e_s"], 95)

            burst_pool_data = [sub_pool[sub_pool["burst_id"] == b]["e2e_s"].values for b in sub_pool["burst_id"].unique()]
            burst_ondemand_data = [sub_ondemand[sub_ondemand["burst_id"] == b]["e2e_s"].values for b in sub_ondemand["burst_id"].unique()]

            ci_med, ci_p95 = compute_bootstrap_ci(burst_ondemand_data, burst_pool_data)

            sp_med = round(med_o / med_p, 3) if med_p > 0 else None
            sp_p95 = round(p95_o / p95_p, 3) if p95_p > 0 else None

            speedup_rows.append({
                "Workload": wl,
                "N": N,
                "Configuration": f"W={W}, B={B}, M={M}",
                "Pool Median (s)": round(med_p, 4),
                "On-Demand Median (s)": round(med_o, 4),
                "Speedup Median": sp_med,
                "95% CI Median": f"[{ci_med[0]}, {ci_med[1]}]" if ci_med[0] else "N/A",
                "Pool P95 (s)": round(p95_p, 4),
                "On-Demand P95 (s)": round(p95_o, 4),
                "Speedup P95": sp_p95,
                "95% CI P95": f"[{ci_p95[0]}, {ci_p95[1]}]" if ci_p95[0] else "N/A"
            })

    df_t3 = pd.DataFrame(table_iii_rows)
    df_t3.to_csv(os.path.join(TABLES_DIR, "table_iii_main_latency.csv"), index=False)

    df_sp = pd.DataFrame(speedup_rows)
    df_sp.to_csv(os.path.join(TABLES_DIR, "speedups_with_intervals.csv"), index=False)

    if not df_t3.empty and "Workload" in df_t3.columns:
        # LaTeX booktabs Table III for Django
        df_t3_dj = df_t3[df_t3["Workload"] == "django"][["N", "Mode", "Total (n)", "Median (s)", "P95 (s)", "Error Rate", "Hit Rate"]]
        with open(os.path.join(TABLES_DIR, "table_iii_django.tex"), "w", encoding="utf-8") as f:
            f.write(df_to_latex(df_t3_dj, float_format="%.3f"))

        # LaTeX booktabs Table III for Express
        df_t3_ex = df_t3[df_t3["Workload"] == "express"][["N", "Mode", "Total (n)", "Median (s)", "P95 (s)", "Error Rate", "Hit Rate"]]
        with open(os.path.join(TABLES_DIR, "table_iii_express.tex"), "w", encoding="utf-8") as f:
            f.write(df_to_latex(df_t3_ex, float_format="%.3f"))

    # 3. Worker Scaling Table (Exp 2 + Final Test)
    exp2_sub = df_sub_valid[(df_sub_valid["experiment"].isin(["2", "final", 2])) & (df_sub_valid["status"] == "completed")]
    if len(exp2_sub) > 0:
        w_scaling = exp2_sub.groupby(["W", "mode"]).agg(
            n=("e2e_s", "count"),
            median_s=("e2e_s", "median"),
            p95_s=("e2e_s", lambda x: np.percentile(x, 95)),
            throughput=("e2e_s", lambda x: round(len(x)/np.sum(x)*4, 3))
        ).reset_index()
        w_scaling.to_csv(os.path.join(TABLES_DIR, "worker_scaling.csv"), index=False)

    # 4. Buffer Effect Table (Exp 3)
    exp3_sub = df_sub_valid[(df_sub_valid["experiment"] == "3") & (df_sub_valid["status"] == "completed")]
    if len(exp3_sub) > 0:
        b_effect = exp3_sub.groupby(["B"]).agg(
            n=("e2e_s", "count"),
            median_s=("e2e_s", "median"),
            p95_s=("e2e_s", lambda x: np.percentile(x, 95)),
            hit_rate=("pool_hit", lambda x: round(float(x.sum()/len(x)), 4))
        ).reset_index()
        b_effect.to_csv(os.path.join(TABLES_DIR, "buffer_effect.csv"), index=False)

    # 5. Ceiling Effect Table (Exp 4)
    exp4_sub = df_sub_valid[df_sub_valid["experiment"] == "4"]
    if len(exp4_sub) > 0:
        m_rows = []
        for M_val in exp4_sub["M"].unique():
            sub_m = exp4_sub[exp4_sub["M"] == M_val]
            n_tot = len(sub_m)
            succ = sub_m[sub_m["status"] == "completed"]
            err_count = n_tot - len(succ)
            err_r = round(err_count / n_tot, 4)
            med = round(float(np.median(succ["e2e_s"])), 4) if len(succ) > 0 else None
            p95 = round(float(np.percentile(succ["e2e_s"], 95)), 4) if len(succ) > 0 else None
            timeouts = sum("timeout" in str(e).lower() for e in sub_m["error_text"])
            m_rows.append({
                "M (Ceiling)": M_val,
                "Total (n)": n_tot,
                "Completed (n)": len(succ),
                "Failed (n)": err_count,
                "Timeouts (n)": timeouts,
                "Error Rate": err_r,
                "Median (s)": med,
                "P95 (s)": p95
            })
        pd.DataFrame(m_rows).sort_values("M (Ceiling)").to_csv(os.path.join(TABLES_DIR, "ceiling_effect.csv"), index=False)

    # 6. Stage Breakdown Table (N=1 and N=50)
    stage_sub = df_success[(df_success["experiment"] == "1") & (df_success["N"].isin([1, 50]))]
    if len(stage_sub) > 0:
        stage_df = stage_sub.groupby(["N", "mode"]).agg(
            workspace_creation=("workspace_creation_time", "median"),
            execution=("execution_time", "median"),
            workspace_cleanup=("workspace_cleanup_time", "median"),
            container_acquire=("container_acquire_time", "median"),
            container_create=("container_create_time", "median"),
            container_destroy=("container_destroy_time", "median"),
            queue_delay=("queue_delay", "median"),
            total_worker_time=("total_worker_time", "median"),
            e2e=("e2e_s", "median")
        ).reset_index()
        stage_df.to_csv(os.path.join(TABLES_DIR, "stage_breakdown.csv"), index=False)

    # -------------------- FIGURES (300 DPI PNG) --------------------
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')

    # Fig 1: Latency vs N for both modes (log y)
    if not df_t3.empty and "Workload" in df_t3.columns and "Experiment" in df_t3.columns:
        exp1_df = df_t3[(df_t3["Experiment"].astype(str) == "1") & (df_t3["Workload"] == "django")]
        if len(exp1_df) > 0:
            plt.figure(figsize=(8, 5), dpi=300)
            p_df = exp1_df[exp1_df["Mode"] == "pool"].sort_values("N")
            o_df = exp1_df[exp1_df["Mode"] == "on_demand"].sort_values("N")

            plt.plot(p_df["N"], p_df["Median (s)"], marker='o', color='#1f77b4', lw=2, label="Pool Median")
            plt.plot(p_df["N"], p_df["P95 (s)"], marker='^', color='#1f77b4', lw=1.5, ls='--', label="Pool P95")
            plt.plot(o_df["N"], o_df["Median (s)"], marker='s', color='#d62728', lw=2, label="On-Demand Median")
            plt.plot(o_df["N"], o_df["P95 (s)"], marker='v', color='#d62728', lw=1.5, ls='--', label="On-Demand P95")

            plt.yscale('log')
            plt.xlabel("Concurrency / Burst Size (N)", fontsize=12, fontweight='bold')
            plt.ylabel("End-to-End Latency (s) [log scale]", fontsize=12, fontweight='bold')
            plt.title("Latency vs Concurrency N (Django, W=4, B=4, M=10)", fontsize=13, fontweight='bold')
            plt.legend(frameon=True, fontsize=10)
            plt.tight_layout()
            plt.savefig(os.path.join(FIGURES_DIR, "latency_vs_N_log.png"), dpi=300)
            plt.close()

    # Fig 2: Latency vs W at N=50
    if len(exp2_sub) > 0 and 'w_scaling' in locals():
        plt.figure(figsize=(8, 5), dpi=300)
        p_w = w_scaling[w_scaling["mode"] == "pool"].sort_values("W")
        o_w = w_scaling[w_scaling["mode"] == "on_demand"].sort_values("W")

        plt.plot(p_w["W"], p_w["median_s"], marker='o', color='#1f77b4', lw=2, label="Pool Median")
        plt.plot(p_w["W"], p_w["p95_s"], marker='^', color='#1f77b4', lw=1.5, ls='--', label="Pool P95")
        plt.plot(o_w["W"], o_w["median_s"], marker='s', color='#d62728', lw=2, label="On-Demand Median")
        plt.plot(o_w["W"], o_w["p95_s"], marker='v', color='#d62728', lw=1.5, ls='--', label="On-Demand P95")

        plt.xlabel("Worker Count (W)", fontsize=12, fontweight='bold')
        plt.ylabel("Latency (s)", fontsize=12, fontweight='bold')
        plt.title("Worker Scaling at N=50 (Django, B=W, M=10)", fontsize=13, fontweight='bold')
        plt.legend(frameon=True, fontsize=10)
        plt.tight_layout()
        plt.savefig(os.path.join(FIGURES_DIR, "latency_vs_W.png"), dpi=300)
        plt.close()

    # Fig 3: Hit Rate and Median Latency vs B
    if len(exp3_sub) > 0 and 'b_effect' in locals():
        fig, ax1 = plt.subplots(figsize=(8, 5), dpi=300)
        ax2 = ax1.twinx()

        b_sorted = b_effect.sort_values("B")
        l1 = ax1.plot(b_sorted["B"], b_sorted["median_s"], marker='o', color='#1f77b4', lw=2, label="Median Latency")
        l2 = ax2.plot(b_sorted["B"], b_sorted["hit_rate"] * 100, marker='s', color='#2ca02c', lw=2, ls='--', label="Hit Rate (%)")

        ax1.set_xlabel("Target Buffer Size (B)", fontsize=12, fontweight='bold')
        ax1.set_ylabel("Median Latency (s)", color='#1f77b4', fontsize=12, fontweight='bold')
        ax2.set_ylabel("Pool Hit Rate (%)", color='#2ca02c', fontsize=12, fontweight='bold')
        ax2.set_ylim(-5, 105)

        lines = l1 + l2
        labels = [l.get_label() for l in lines]
        ax1.legend(lines, labels, loc='center right', frameon=True)
        plt.title("Effect of Target Buffer B (Django, W=8, N=50, M=10)", fontsize=13, fontweight='bold')
        plt.tight_layout()
        plt.savefig(os.path.join(FIGURES_DIR, "hitrate_latency_vs_B.png"), dpi=300)
        plt.close()

    # Fig 4: Error Rate and Latency vs M
    if len(exp4_sub) > 0 and 'm_rows' in locals():
        fig, ax1 = plt.subplots(figsize=(8, 5), dpi=300)
        ax2 = ax1.twinx()

        df_m = pd.DataFrame(m_rows).sort_values("M (Ceiling)")
        l1 = ax1.plot(df_m["M (Ceiling)"], df_m["Median (s)"], marker='o', color='#1f77b4', lw=2, label="Median Latency (Successes)")
        l2 = ax2.plot(df_m["M (Ceiling)"], df_m["Error Rate"] * 100, marker='d', color='#d62728', lw=2, ls='--', label="Error Rate (%)")

        ax1.set_xlabel("Max Pool Ceiling (M)", fontsize=12, fontweight='bold')
        ax1.set_ylabel("Median Latency (s)", color='#1f77b4', fontsize=12, fontweight='bold')
        ax2.set_ylabel("Error Rate (%)", color='#d62728', fontsize=12, fontweight='bold')
        ax2.set_ylim(-5, 105)

        lines = l1 + l2
        labels = [l.get_label() for l in lines]
        ax1.legend(lines, labels, loc='center right', frameon=True)
        plt.title("Ceiling Effect & Starvation vs M (Django, W=8, N=50)", fontsize=13, fontweight='bold')
        plt.tight_layout()
        plt.savefig(os.path.join(FIGURES_DIR, "error_latency_vs_M.png"), dpi=300)
        plt.close()

    # Fig 5: Idle/Busy Timeline of Representative N=50 Pool Burst
    sample_files = [f for f in os.listdir(os.path.join(RAW_DIR, "samples")) if ("exp1" in f and "N50" in f and "pool" in f) or ("N50_pool" in f)]
    if sample_files:
        sample_path = os.path.join(RAW_DIR, "samples", sample_files[0])
        s_df = pd.read_csv(sample_path)
        plt.figure(figsize=(9, 4.5), dpi=300)
        plt.plot(s_df["rel_time_s"], s_df["django_idle"], label="Django Idle Containers", color='#2ca02c', lw=2)
        plt.plot(s_df["rel_time_s"], s_df["django_busy"], label="Django Busy Containers", color='#d62728', lw=2)
        plt.plot(s_df["rel_time_s"], s_df["queue_len"], label="RQ Queue Length", color='#ff7f0e', lw=1.5, ls=':')

        plt.xlabel("Time Since Burst Start (s)", fontsize=12, fontweight='bold')
        plt.ylabel("Count", fontsize=12, fontweight='bold')
        plt.title(f"Pool Dynamic Scaling Timeline (N=50 Burst, W=4, B=4, M=10)", fontsize=13, fontweight='bold')
        plt.legend(frameon=True, fontsize=10)
        plt.tight_layout()
        plt.savefig(os.path.join(FIGURES_DIR, "pool_timeline_N50.png"), dpi=300)
        plt.close()

    # Fig 6: Stacked Stage Breakdown at N=1 and N=50
    if len(stage_sub) > 0 and 'stage_df' in locals():
        fig, axes = plt.subplots(1, 2, figsize=(10, 5), dpi=300, sharey=True)
        stages = ["Queue Delay", "Workspace Setup", "Test Execution", "Workspace Cleanup", "Acquire / Create"]

        for idx, N_val in enumerate([1, 50]):
            ax = axes[idx]
            sub_n = stage_df[stage_df["N"] == N_val]
            if len(sub_n) == 0:
                continue

            modes = ["pool", "on_demand"]
            q_d = [float(sub_n[sub_n["mode"] == m]["queue_delay"].values[0] or 0) for m in modes]
            ws_c = [float(sub_n[sub_n["mode"] == m]["workspace_creation"].values[0] or 0) for m in modes]
            exec_t = [float(sub_n[sub_n["mode"] == m]["execution"].values[0] or 0) for m in modes]
            ws_cl = [float(sub_n[sub_n["mode"] == m]["workspace_cleanup"].values[0] or 0) for m in modes]
            acq_cr = [float(sub_n[sub_n["mode"] == m]["container_acquire"].values[0] or 0) if m == "pool" else float(sub_n[sub_n["mode"] == m]["container_create"].values[0] or 0) for m in modes]

            b_bottom = np.array([0.0, 0.0])
            ax.bar(modes, q_d, label="Queue Delay" if idx == 0 else "", bottom=b_bottom, color='#bcbd22')
            b_bottom += np.array(q_d)
            ax.bar(modes, acq_cr, label="Container Acquire / Create" if idx == 0 else "", bottom=b_bottom, color='#17becf')
            b_bottom += np.array(acq_cr)
            ax.bar(modes, ws_c, label="Workspace Setup" if idx == 0 else "", bottom=b_bottom, color='#1f77b4')
            b_bottom += np.array(ws_c)
            ax.bar(modes, exec_t, label="Test Execution" if idx == 0 else "", bottom=b_bottom, color='#2ca02c')
            b_bottom += np.array(exec_t)
            ax.bar(modes, ws_cl, label="Workspace Cleanup" if idx == 0 else "", bottom=b_bottom, color='#d62728')

            ax.set_title(f"Concurrency N = {N_val}", fontsize=12, fontweight='bold')
            ax.set_ylabel("Duration (s)" if idx == 0 else "", fontsize=11)

        fig.legend(loc='upper center', bbox_to_anchor=(0.5, 1.05), ncol=5, frameon=True, fontsize=9)
        plt.tight_layout()
        plt.savefig(os.path.join(FIGURES_DIR, "stacked_stage_breakdown.png"), dpi=300, bbox_inches='tight')
        plt.close()

    print("[Analysis] All tables and figures updated successfully.")

if __name__ == "__main__":
    generate_all_tables_and_figures()
