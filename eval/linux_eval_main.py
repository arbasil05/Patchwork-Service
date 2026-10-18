#!/usr/bin/env python3
"""
Linux Evaluation Orchestrator for Patchwork-Service
Runs Parts 0-4, writes results to eval/results_linux/
"""
import asyncio, os, sys, time, json, random, subprocess, signal, socket, platform, statistics, logging

sys.path.insert(0, os.path.abspath("eval"))
import numpy as np
import pandas as pd
from datetime import datetime, timezone

RESULTS_DIR = os.path.abspath("eval/results_linux")
RAW_DIR    = os.path.join(RESULTS_DIR, "raw")
TABLES_DIR = os.path.join(RESULTS_DIR, "tables")
FIGURES_DIR= os.path.join(RESULTS_DIR, "figures")
LOGS_DIR   = os.path.join(RESULTS_DIR, "logs")
GATES_DIR  = os.path.join(RESULTS_DIR, "gates")
BACKEND_DIR= os.path.abspath("backend")
PYTHON     = os.path.join(BACKEND_DIR, "venv", "bin", "python")
PROGRESS_MD  = os.path.join(RESULTS_DIR, "PROGRESS.md")
PROGRESS_JSON= os.path.join(RESULTS_DIR, "progress.json")
MANIFEST_FILE= os.path.join(RESULTS_DIR, "manifest.json")
GIT_SHA    = "705b3c28ec7475a5b4c04611ea6cc75c13cb1d38"
RANDOM_SEED= 42
START_TIME = time.time()
SUBMISSIONS_CSV = os.path.join(RAW_DIR, "submissions.csv")
BURSTS_CSV      = os.path.join(RAW_DIR, "bursts.csv")

for d in [RESULTS_DIR, RAW_DIR, TABLES_DIR, FIGURES_DIR, LOGS_DIR, GATES_DIR,
          os.path.join(RAW_DIR, "samples")]:
    os.makedirs(d, exist_ok=True)

log_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
logger  = logging.getLogger("linux_eval")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler(os.path.join(LOGS_DIR, "eval.log"), encoding="utf-8")
fh.setFormatter(log_fmt)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(log_fmt)
logger.addHandler(fh)
logger.addHandler(ch)

SUB_HEADERS = [
    "burst_id","experiment","cell","mode","workload","N","W","B","M",
    "repetition","is_warmup","submission_id","completion_source","client_submit_s",
    "e2e_s","status","exit_code","expected_exit_code","correct","error_text",
    "workspace_creation_time","execution_time","workspace_cleanup_time","total_worker_time",
    "queue_delay","container_acquire_time","pool_hit","container_create_time",
    "container_destroy_time","rq_enqueued_at","rq_started_at","rq_ended_at"
]
BURST_HEADERS = [
    "burst_id","experiment","cell","mode","workload","N","W","B","M",
    "repetition","is_warmup","burst_duration_s","throughput_req_per_s",
    "count_total","count_success","count_failed","p50_e2e_s","p95_e2e_s",
    "p99_e2e_s","success_rate","host_cpu_mean","host_cpu_max",
    "min_free_mem_mb","throttled","oom_killed",
    "harness_cpu_pct","load_avg_1m","dockerd_cpu_pct"
]

def load_progress():
    if os.path.exists(PROGRESS_JSON):
        try: return json.load(open(PROGRESS_JSON))
        except: return {}
    return {}

def save_progress(d):
    with open(PROGRESS_JSON,"w") as f: json.dump(d,f,indent=2)

def update_progress_md(step, status, notes=""):
    elapsed=(time.time()-START_TIME)/3600.0
    content=f"# Linux Evaluation Progress\n\n**Last Updated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n**Elapsed**: {elapsed:.2f}h\n**Step**: {step} — {status}\n\n{notes}\n"
    with open(PROGRESS_MD,"w",encoding="utf-8") as f: f.write(content)

def abort_check():
    for bat in os.listdir("/sys/class/power_supply/"):
        sf=f"/sys/class/power_supply/{bat}/status"
        if os.path.exists(sf) and open(sf).read().strip()=="Discharging":
            raise SystemExit("ABORT: Battery discharging")
    st=os.statvfs("/")
    if st.f_bavail*st.f_frsize/1e9 < 5:
        raise SystemExit("ABORT: <5GB disk free")
    with open("/proc/meminfo") as f:
        mi=dict(l.split(":")[:2] for l in f if ":" in l)
    tot=int(mi["MemTotal"].strip().split()[0])
    avail=int(mi["MemAvailable"].strip().split()[0])
    if avail < 0.10*tot: raise SystemExit("ABORT: MemAvailable <10%")

def _ensure_header(path, headers):
    if not os.path.exists(path) or os.path.getsize(path)==0:
        with open(path,"w",encoding="utf-8") as f: f.write(",".join(headers)+"\n")

def append_submissions(rows):
    _ensure_header(SUBMISSIONS_CSV, SUB_HEADERS)
    with open(SUBMISSIONS_CSV,"a",encoding="utf-8") as f:
        for row in rows:
            f.write(",".join(""if row.get(h)is None else str(row[h]) for h in SUB_HEADERS)+"\n")

def append_burst(row):
    _ensure_header(BURSTS_CSV, BURST_HEADERS)
    with open(BURSTS_CSV,"a",encoding="utf-8") as f:
        f.write(",".join(""if row.get(h)is None else str(row[h]) for h in BURST_HEADERS)+"\n")

def make_schedule(modes, r_counts, seed=42):
    warmups=[(m,0,True) for m in modes]
    if len(modes)==1:
        m=modes[0]; r=r_counts[m]
        return warmups+[(m,i+1,False) for i in range(r)]
    rng=random.Random(seed)
    bursts=[]
    for m in modes:
        bursts.extend([(m,i+1,False) for i in range(r_counts[m])])
    for _ in range(2000):
        rng.shuffle(bursts)
        if all(not(bursts[j][0]==bursts[j+1][0]==bursts[j+2][0]) for j in range(len(bursts)-2)):
            break
    return warmups+bursts


# ============================================================
# PROCESS MANAGER
# ============================================================
class LinuxProcessManager:
    def __init__(self, redis_host="127.0.0.1", redis_port=6379):
        import redis, rq
        self.r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.r_raw = redis.Redis(host=redis_host, port=redis_port, decode_responses=False)
        self.q = rq.Queue("submission_queue", connection=self.r_raw)
        self.daemon_proc=None; self.worker_procs=[]; self.api_proc=None; self.last_burst_end=0.0

    def start_api(self):
        try:
            subprocess.run(["pkill", "-f", "uvicorn main:app"], capture_output=True)
            time.sleep(1)
        except: pass
        env=os.environ.copy(); env.update({"PYTHONIOENCODING":"utf-8","AUTH_MODE":"none","REDIS_HOST":"localhost"})
        api_log=open(os.path.join(LOGS_DIR,"api.log"),"a",encoding="utf-8")
        self.api_proc=subprocess.Popen(
            [PYTHON,"-m","uvicorn","main:app","--host","0.0.0.0","--port","8000"],
            cwd=BACKEND_DIR,env=env,stdout=api_log,stderr=subprocess.STDOUT,start_new_session=True)
        logger.info(f"Started API PID={self.api_proc.pid}")
        for _ in range(40):
            try:
                s=socket.create_connection(("127.0.0.1",8000),timeout=1); s.close(); logger.info("API ready"); return True
            except: time.sleep(0.5)
        logger.error("API failed to start"); return False

    def stop_api(self):
        if self.api_proc:
            try: os.killpg(os.getpgid(self.api_proc.pid),signal.SIGTERM); self.api_proc.wait(timeout=5)
            except:
                try: self.api_proc.kill()
                except: pass
            self.api_proc=None

    def set_pool_max(self, pool_name, mc):
        cmd=[PYTHON,"-c",
             f"import sys; sys.path.insert(0,'{BACKEND_DIR}'); "
             f"from storage.pool_defs import update_pool_def; "
             f"update_pool_def('{pool_name}', {{'max_containers': {mc}}})"]
        r=subprocess.run(cmd,cwd=BACKEND_DIR,capture_output=True,text=True)
        if r.returncode!=0: raise RuntimeError(f"set_pool_max failed: {r.stderr}")
        logger.info(f"Set {pool_name} max_containers={mc}")

    def start_daemon(self, B=3, M=10, ci=2.0):
        self.stop_daemon()
        env=os.environ.copy(); env.update({"PYTHONIOENCODING":"utf-8","TARGET_BUFFER":str(B),
            "MAX_POOL_SIZE":str(M),"CHECK_INTERVAL":str(ci),"REDIS_HOST":"localhost","AUTH_MODE":"none"})
        dl=open(os.path.join(LOGS_DIR,"daemon.log"),"a",encoding="utf-8")
        ds=os.path.join(BACKEND_DIR,"tasks","pool","pool_daemon.py")
        self.daemon_proc=subprocess.Popen([PYTHON,ds],cwd=os.path.join(BACKEND_DIR,"tasks","pool"),
            env=env,stdout=dl,stderr=subprocess.STDOUT,start_new_session=True)
        logger.info(f"Daemon PID={self.daemon_proc.pid} B={B} M={M}")

    def stop_daemon(self):
        if self.daemon_proc:
            try: os.killpg(os.getpgid(self.daemon_proc.pid),signal.SIGTERM); self.daemon_proc.wait(timeout=8)
            except:
                try: self.daemon_proc.kill()
                except: pass
            self.daemon_proc=None
        try:
            r=subprocess.run(["pgrep","-f","pool_daemon.py"],capture_output=True,text=True)
            for pid in r.stdout.strip().splitlines():
                try: os.kill(int(pid),signal.SIGTERM)
                except: pass
        except: pass

    def start_workers(self, count, cell_name="cell"):
        self.stop_workers()
        import rq
        env=os.environ.copy(); env.update({"PYTHONIOENCODING":"utf-8","EVAL_MODE_SWITCH":"1","REDIS_HOST":"localhost","AUTH_MODE":"none"})
        ws=os.path.join("worker","run_worker.py")
        for i in range(count):
            lf=open(os.path.join(LOGS_DIR,f"worker_{cell_name}_{i+1}.log"),"w",encoding="utf-8")
            p=subprocess.Popen([PYTHON,ws],cwd=BACKEND_DIR,env=env,stdout=lf,stderr=subprocess.STDOUT,start_new_session=True)
            self.worker_procs.append(p)
        t0=time.time()
        while time.time()-t0<20:
            ws_all=rq.Worker.all(connection=self.r)
            if len(ws_all)>=count and all(w.get_state() in ("idle","started") for w in ws_all):
                logger.info(f"Verified {count} workers"); return
            time.sleep(0.5)
        logger.warning(f"Expected {count} workers, got {len(rq.Worker.all(connection=self.r))}")

    def stop_workers(self):
        import rq
        for p in self.worker_procs:
            try: os.killpg(os.getpgid(p.pid),signal.SIGTERM); p.wait(timeout=5)
            except:
                try: p.kill()
                except: pass
        self.worker_procs.clear()
        try:
            r=subprocess.run(["pgrep","-f","run_worker.py"],capture_output=True,text=True)
            for pid in r.stdout.strip().splitlines():
                try: os.kill(int(pid),signal.SIGTERM)
                except: pass
        except: pass
        time.sleep(1)
        try:
            for w in rq.Worker.all(connection=self.r):
                try: w.clean_registrations()
                except: pass
            wk=self.r.keys("rq:worker:*")
            if wk: self.r.delete(*wk)
            self.r.delete("rq:workers")
        except: pass

    def wait_steady(self, img, B, M, N, ci=2.0):
        import rq
        idle_key=f"pool:{img}:idle"
        exp_idle=min(B,M)
        if self.last_burst_end>0:
            gap=time.time()-self.last_burst_end
            if gap<2*ci: time.sleep(2*ci-gap)
        consec=0; t0=time.time()
        while time.time()-t0<120:
            q_len=self.r.llen("rq:queue:submission_queue")
            dj_b=self.r.scard("pool:patchwork-django:latest:busy")
            ex_b=self.r.scard("pool:patchwork-express:latest:busy")
            try: started=len(self.q.started_job_registry.get_job_ids())
            except: started=0
            cur_idle=self.r.scard(idle_key)
            if q_len==0 and dj_b==0 and ex_b==0 and started==0 and cur_idle==exp_idle:
                consec+=1
                if consec>=3: break
            else: consec=0
            time.sleep(0.5)
        if consec<3: logger.warning(f"[Gate] timeout idle={self.r.scard(idle_key)} exp={exp_idle}")
        cool=30 if N>=50 else 10 if N>=10 else 2
        logger.info(f"[Gate] Steady (idle={exp_idle}). Cooldown {cool}s"); time.sleep(cool)

    def record_burst_end(self): self.last_burst_end=time.time()

    def mem_budget_ok(self, W, M, p95_mb, rss_mb):
        with open("/proc/meminfo") as f:
            mi=dict(l.split(":")[:2] for l in f if ":" in l)
        avail_mb=int(mi["MemAvailable"].strip().split()[0])/1024
        est=M*p95_mb+W*rss_mb; budget=0.60*avail_mb
        logger.info(f"Mem est: {est:.0f}MB vs budget {budget:.0f}MB (avail={avail_mb:.0f}MB)")
        return est<=budget


# ============================================================
# SAMPLER
# ============================================================
class LinuxSampler:
    def __init__(self, burst_id):
        import redis, rq
        self.burst_id=burst_id
        self.r=redis.Redis(host="127.0.0.1",port=6379,decode_responses=True)
        self.q=rq.Queue("submission_queue",connection=self.r)
        self.running=False; self.samples=[]; self.throttled=False; self.oom_killed=False
        self.min_free_mb=float("inf"); self.cpu_vals=[]; self.load_vals=[]; self.harness_vals=[]; self.dockerd_vals=[]
        self._task=None; self.t0=0.0; self._pid=os.getpid()

    async def _loop(self):
        import psutil
        self.t0=time.perf_counter(); next1=self.t0+1.0
        cpu=psutil.cpu_percent(interval=None); mem=psutil.virtual_memory()
        avail_mb=mem.available/1048576; ctrs=0; load1=0.0; h_cpu=0.0; d_cpu=0.0
        while self.running:
            now=time.perf_counter(); rel=now-self.t0
            if now>=next1:
                next1=now+1.0
                cpu=psutil.cpu_percent(interval=None); self.cpu_vals.append(cpu)
                mem=psutil.virtual_memory(); avail_mb=mem.available/1048576
                if avail_mb<self.min_free_mb: self.min_free_mb=avail_mb
                mem_total=mem.total
                if mem.available<0.15*mem_total: logger.warning("WATCHDOG: MemAvailable <15%!")
                try:
                    la=os.getloadavg(); load1=la[0]; self.load_vals.append(load1)
                except: pass
                try:
                    cur=float(open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read())
                    mx=float(open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq").read())
                    if mx>0 and (cur/mx)*100<70: self.throttled=True
                except: pass
                try:
                    hp=psutil.Process(self._pid); h_cpu=hp.cpu_percent(interval=None); self.harness_vals.append(h_cpu)
                    if h_cpu>70: logger.warning(f"Client-bound: harness CPU={h_cpu:.1f}%")
                except: pass
                try:
                    dc=sum(p.cpu_percent(interval=None) or 0 for p in psutil.process_iter(['name','cpu_percent'])
                           if p.info['name'] in ('dockerd','containerd'))
                    self.dockerd_vals.append(dc); d_cpu=dc
                except: pass
                try:
                    rr=subprocess.run(["docker","ps","-q"],capture_output=True,text=True,timeout=2)
                    ctrs=len([x for x in rr.stdout.strip().splitlines() if x])
                except: pass
            try:
                pipe=self.r.pipeline()
                pipe.scard("pool:patchwork-django:latest:idle"); pipe.scard("pool:patchwork-django:latest:busy")
                pipe.scard("pool:patchwork-express:latest:idle"); pipe.scard("pool:patchwork-express:latest:busy")
                pipe.llen("rq:queue:submission_queue")
                di,db,ei,eb,ql=pipe.execute()
            except: di=db=ei=eb=ql=0
            try: st=self.q.started_job_registry.count
            except: st=0
            self.samples.append({
                "rel_time_s":round(rel,4),"django_idle":di,"django_busy":db,"express_idle":ei,"express_busy":eb,
                "queue_len":ql,"started_jobs":st,"cpu_percent":cpu,"memory_available_mb":round(avail_mb,2),
                "running_containers":ctrs,"load_avg_1m":round(load1,3),"harness_cpu_pct":round(h_cpu,2),
                "dockerd_cpu_pct":round(d_cpu,2),"throttled":int(self.throttled),"oom_killed":int(self.oom_killed)})
            await asyncio.sleep(0.25)

    def start(self):
        self.running=True; self._task=asyncio.create_task(self._loop())

    async def stop(self):
        self.running=False
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
        try:
            rr=subprocess.run(["docker","ps","-a","--filter","status=exited","--format","{{.ID}}"],
                              capture_output=True,text=True,timeout=3)
            for cid in rr.stdout.strip().splitlines():
                if cid.strip():
                    ir=subprocess.run(["docker","inspect",cid.strip(),"--format","{{.State.OOMKilled}}"],
                                      capture_output=True,text=True,timeout=2)
                    if ir.stdout.strip()=="true": self.oom_killed=True; break
        except: pass
        od=os.path.join(RAW_DIR,"samples"); os.makedirs(od,exist_ok=True)
        hdrs=["rel_time_s","django_idle","django_busy","express_idle","express_busy","queue_len","started_jobs",
              "cpu_percent","memory_available_mb","running_containers","load_avg_1m","harness_cpu_pct","dockerd_cpu_pct",
              "throttled","oom_killed"]
        with open(os.path.join(od,f"{self.burst_id}.csv"),"w",encoding="utf-8") as f:
            f.write(",".join(hdrs)+"\n")
            for s in self.samples: f.write(",".join(str(s.get(h,"")) for h in hdrs)+"\n")

    def summary(self):
        avg=lambda v: statistics.mean(v) if v else 0.0
        return {"host_cpu_mean":round(avg(self.cpu_vals),2),"host_cpu_max":round(max(self.cpu_vals)if self.cpu_vals else 0,2),
                "min_free_mem_mb":round(self.min_free_mb,2) if self.min_free_mb!=float("inf") else 0.0,
                "throttled":self.throttled,"oom_killed":self.oom_killed,
                "load_avg_1m":round(avg(self.load_vals),3),"harness_cpu_pct":round(avg(self.harness_vals),2),
                "dockerd_cpu_pct":round(avg(self.dockerd_vals),2)}


# ============================================================
# BURST RUNNER
# ============================================================
class LinuxRunner:
    def __init__(self):
        import redis; from rq.job import Job; import json as _j
        self.r=redis.Redis(host="127.0.0.1",port=6379,decode_responses=True)
        self.r_raw=redis.Redis(host="127.0.0.1",port=6379,decode_responses=False)
        self.Job=Job
        with open("eval/payloads/django_pass.json") as f: self.djp=_j.load(f)
        with open("eval/payloads/express_pass.json") as f: self.exp=_j.load(f)

    async def run_burst(self, experiment, cell, burst_id, mode, workload, N, W, B, M,
                        repetition, is_warmup, receiver, sampler):
        import copy, uuid, httpx
        tasks_p=[]
        for i in range(N):
            jid=str(uuid.uuid4())
            if workload=="django": p=copy.deepcopy(self.djp); ex=0
            elif workload=="express": p=copy.deepcopy(self.exp); ex=0
            else: p=copy.deepcopy(self.djp if i<N//2 else self.exp); ex=0
            p["idempotency_key"]=jid; p["metadata"]={"execution_mode":mode}
            p["callback_url"]=f"http://{receiver.host}:{receiver.port}/webhook"
            receiver.register_job(jid); tasks_p.append((jid,p,ex))

        barrier=asyncio.Event(); sampler.start(); t0=time.perf_counter()

        async def _sub(jid,payload,ex,client):
            await barrier.wait(); ts=time.perf_counter(); rtt=0.0
            try:
                resp=await client.post("http://127.0.0.1:8000/v1/execute",json=payload,timeout=10.0)
                rtt=time.perf_counter()-ts; rcode=resp.status_code
            except Exception as e:
                return {"submission_id":jid,"t_submit":ts,"post_rtt":rtt,"t_complete":time.perf_counter(),
                        "completion_source":"error","status":"failed","exit_code":-1,"expected_exit_code":ex,
                        "correct":False,"error_text":f"POST failed:{e}","worker_metrics":{}}
            if rcode!=202:
                return {"submission_id":jid,"t_submit":ts,"post_rtt":rtt,"t_complete":time.perf_counter(),
                        "completion_source":"error","status":"failed","exit_code":-1,"expected_exit_code":ex,
                        "correct":False,"error_text":f"POST {rcode}","worker_metrics":{}}
            tmo=max(90.0,float(N)*2.5)
            wh=await receiver.wait_for_job(jid,timeout=tmo)
            if wh:
                pl=wh["payload"]; st=pl.get("status","failed"); ec=pl.get("exit_code")
                return {"submission_id":jid,"t_submit":ts,"post_rtt":rtt,"t_complete":wh["received_at"],
                        "completion_source":"webhook","status":st,"exit_code":ec,"expected_exit_code":ex,
                        "correct":ec==ex,"error_text":pl.get("stderr")or"","worker_metrics":pl.get("metadata",{})}
            ps=time.perf_counter()
            while time.perf_counter()-ps<45:
                await asyncio.sleep(2)
                try:
                    sr=await client.get(f"http://127.0.0.1:8000/v1/status/{jid}",timeout=5)
                    if sr.status_code==200:
                        sd=sr.json(); st=sd.get("status")
                        if st in("completed","failed"):
                            ec=sd.get("exit_code")
                            return {"submission_id":jid,"t_submit":ts,"post_rtt":rtt,"t_complete":time.perf_counter(),
                                    "completion_source":"poll","status":st,"exit_code":ec,"expected_exit_code":ex,
                                    "correct":ec==ex,"error_text":sd.get("stderr")or"","worker_metrics":sd.get("metadata",{})}
                except: pass
            return {"submission_id":jid,"t_submit":ts,"post_rtt":rtt,"t_complete":time.perf_counter(),
                    "completion_source":"timeout","status":"failed","exit_code":-1,"expected_exit_code":ex,
                    "correct":False,"error_text":f"Timeout {tmo+45}s","worker_metrics":{}}

        lim=httpx.Limits(max_keepalive_connections=None,max_connections=None)
        async with httpx.AsyncClient(limits=lim,timeout=max(180.0,float(N)*3.5)) as c:
            tasks=[_sub(u,p,e,c) for u,p,e in tasks_p]; barrier.set(); results=await asyncio.gather(*tasks)

        dur=time.perf_counter()-t0; await sampler.stop(); sm=sampler.summary()

        subs=[]
        for res in results:
            jid=res["submission_id"]; eq=ra=rs=""
            try:
                rj=self.Job.fetch(jid,connection=self.r_raw)
                if rj.enqueued_at: eq=rj.enqueued_at.timestamp()
                if rj.started_at: ra=rj.started_at.timestamp()
                if rj.ended_at: rs=rj.ended_at.timestamp()
                if rj.result and isinstance(rj.result,dict):
                    for k,v in (rj.result.get("metrics",{})or{}).items():
                        if k not in res["worker_metrics"] or res["worker_metrics"][k]is None: res["worker_metrics"][k]=v
            except: pass
            wm=res["worker_metrics"]; e2e=res["t_complete"]-res["t_submit"]
            subs.append({
                "burst_id":burst_id,"experiment":experiment,"cell":cell,"mode":mode,"workload":workload,
                "N":N,"W":W,"B":B,"M":M,"repetition":repetition,"is_warmup":int(is_warmup),
                "submission_id":jid,"completion_source":res["completion_source"],
                "client_submit_s":round(res["t_submit"],6),"e2e_s":round(e2e,6),
                "status":res["status"],"exit_code":res["exit_code"],"expected_exit_code":res["expected_exit_code"],
                "correct":int(res["correct"]),"error_text":str(res["error_text"]).replace(",",";").replace("\n"," "),
                "workspace_creation_time":wm.get("workspace_creation_time"),"execution_time":wm.get("execution_time"),
                "workspace_cleanup_time":wm.get("workspace_cleanup_time"),"total_worker_time":wm.get("total_worker_time"),
                "queue_delay":wm.get("queue_delay"),"container_acquire_time":wm.get("container_acquire_time"),
                "pool_hit":1 if wm.get("pool_hit")is True else(0 if wm.get("pool_hit")is False else ""),
                "container_create_time":wm.get("container_create_time"),"container_destroy_time":wm.get("container_destroy_time"),
                "rq_enqueued_at":eq,"rq_started_at":ra,"rq_ended_at":rs})

        append_submissions(subs)
        ok=[r["e2e_s"] for r in subs if r["status"]=="completed" and r["correct"]==1]
        sc=len(ok); fc=N-sc
        p50=round(float(np.percentile(ok,50)),6) if ok else None
        p95=round(float(np.percentile(ok,95)),6) if ok else None
        p99=round(float(np.percentile(ok,99)),6) if ok else None
        tput=round(N/dur,4) if dur>0 else 0
        br={"burst_id":burst_id,"experiment":experiment,"cell":cell,"mode":mode,"workload":workload,
            "N":N,"W":W,"B":B,"M":M,"repetition":repetition,"is_warmup":int(is_warmup),
            "burst_duration_s":round(dur,4),"throughput_req_per_s":tput,"count_total":N,
            "count_success":sc,"count_failed":fc,"p50_e2e_s":p50,"p95_e2e_s":p95,"p99_e2e_s":p99,
            "success_rate":round(sc/N,4) if N>0 else 0,"host_cpu_mean":sm["host_cpu_mean"],
            "host_cpu_max":sm["host_cpu_max"],"min_free_mem_mb":sm["min_free_mem_mb"],
            "throttled":int(sm["throttled"]),"oom_killed":int(sm["oom_killed"]),
            "harness_cpu_pct":sm.get("harness_cpu_pct",0),"load_avg_1m":sm.get("load_avg_1m",0),
            "dockerd_cpu_pct":sm.get("dockerd_cpu_pct",0)}
        append_burst(br)
        logger.info(f"[Burst] {burst_id}: {dur:.1f}s {sc}/{N} p50={p50} p95={p95} tput={tput}/s")
        return br


# ============================================================
# GATES
# ============================================================
async def run_gate_v1(pm, receiver):
    logger.info("=== Gate V1 ===")
    import json as _j, httpx, uuid, copy
    pls={}
    for nm in ["django_pass","django_fail","express_pass","express_fail"]:
        with open(f"eval/payloads/{nm}.json") as f: pls[nm]=_j.load(f)
    tests=[("Django Passing",pls["django_pass"],0),("Django Failing",pls["django_fail"],1),
           ("Express Passing",pls["express_pass"],0),("Express Failing",pls["express_fail"],1)]
    results={}; ok_all=True
    async with httpx.AsyncClient(timeout=90.0) as c:
        for nm,pl,ex in tests:
            jid=str(uuid.uuid4()); p=copy.deepcopy(pl)
            p["idempotency_key"]=jid; p["callback_url"]=f"http://{receiver.host}:{receiver.port}/webhook"
            p["metadata"]={"execution_mode":"on_demand"}; receiver.register_job(jid)
            resp=await c.post("http://127.0.0.1:8000/v1/execute",json=p)
            if resp.status_code!=202:
                logger.error(f"[V1] {nm} POST={resp.status_code}"); ok_all=False
                results[nm]={"passed":False,"error":f"POST {resp.status_code}"}; continue
            wh=await receiver.wait_for_job(jid,timeout=90.0)
            if not wh:
                logger.error(f"[V1] {nm} timeout"); ok_all=False
                results[nm]={"passed":False,"error":"timeout"}; continue
            pl2=wh["payload"]; st=pl2.get("status"); ec=pl2.get("exit_code")
            ok=(st=="completed" and ec==ex)
            logger.info(f"[V1] {nm}: ok={ok} status={st} exit={ec} expected={ex}")
            results[nm]={"passed":ok,"status":st,"exit_code":ec}
            if not ok: ok_all=False
    r={"passed":ok_all,"results":results}
    with open(os.path.join(GATES_DIR,"v1.json"),"w") as f: _j.dump(r,f,indent=2)
    logger.info(f"Gate V1: {'PASS' if ok_all else 'FAIL'}")
    return ok_all

async def run_gate_v3(pm, receiver):
    logger.info("=== Gate V3 ===")
    import json as _j, httpx, uuid, copy
    with open("eval/payloads/django_pass.json") as f: pl=_j.load(f)
    pool_hc=None
    try:
        rr=subprocess.run(["docker","exec","patchwork-redis-1","redis-cli",
                           "smembers","pool:patchwork-django:latest:idle"],
                          capture_output=True,text=True,timeout=5)
        pids=[x.strip() for x in rr.stdout.strip().splitlines() if x.strip()]
        if pids:
            r2=subprocess.run(["docker","inspect",pids[0],"--format","{{json .HostConfig}}"],
                              capture_output=True,text=True,timeout=5)
            pool_hc=_j.loads(r2.stdout.strip())
    except Exception as e: logger.warning(f"[V3] HostConfig: {e}")
    rb=subprocess.run(["docker","ps","-q"],capture_output=True,text=True,timeout=3)
    before=set(x.strip() for x in rb.stdout.strip().splitlines() if x.strip())
    jid=str(uuid.uuid4()); p=copy.deepcopy(pl)
    p["idempotency_key"]=jid; p["metadata"]={"execution_mode":"on_demand"}
    p["callback_url"]=f"http://{receiver.host}:{receiver.port}/webhook"; receiver.register_job(jid)
    async with httpx.AsyncClient(timeout=90.0) as c:
        resp=await c.post("http://127.0.0.1:8000/v1/execute",json=p)
        if resp.status_code!=202:
            r={"passed":False,"error":f"POST {resp.status_code}"}
            with open(os.path.join(GATES_DIR,"v3.json"),"w") as f: _j.dump(r,f,indent=2)
            return False
        await asyncio.sleep(0.5)
        rd=subprocess.run(["docker","ps","-q"],capture_output=True,text=True,timeout=3)
        during=set(x.strip() for x in rd.stdout.strip().splitlines() if x.strip())
        new=during-before
        wh=await receiver.wait_for_job(jid,timeout=90.0)
    if not wh:
        r={"passed":False,"error":"timeout"}
        with open(os.path.join(GATES_DIR,"v3.json"),"w") as f: _j.dump(r,f,indent=2)
        return False
    await asyncio.sleep(2)
    ra=subprocess.run(["docker","ps","-q"],capture_output=True,text=True,timeout=3)
    after=set(x.strip() for x in ra.stdout.strip().splitlines() if x.strip()); still=new&after
    hc_match=None
    if new and pool_hc:
        try:
            cid=list(new)[0]
            r2=subprocess.run(["docker","inspect",cid,"--format","{{json .HostConfig}}"],
                              capture_output=True,text=True,timeout=5)
            od_hc=_j.loads(r2.stdout.strip())
            hc_match=all(pool_hc.get(k)==od_hc.get(k) for k in ["NetworkMode","Memory","CpuShares","AutoRemove"])
        except Exception as e: logger.warning(f"[V3] HostConfig compare: {e}")
    e1=len(new)==1; d=len(still)==0; passed=e1 and d
    result={"passed":passed,"new_containers":len(new),"destroyed":d,"host_config_match":hc_match,
            "webhook_status":wh["payload"].get("status"),"exit_code":wh["payload"].get("exit_code")}
    with open(os.path.join(GATES_DIR,"v3.json"),"w") as f: _j.dump(result,f,indent=2)
    logger.info(f"Gate V3: {'PASS' if passed else 'FAIL'} new={len(new)} destroyed={d}")
    return passed

async def run_cell(pm, receiver, runner, progress, cell_info):
    cn=cell_info["cell"]; exp=cell_info["exp"]; wl=cell_info["workload"]
    N=cell_info["N"]; W=cell_info["W"]; B=cell_info["B"]; M=cell_info["M"]
    modes=cell_info["modes"]; rc=cell_info["r"]; ds=cell_info.get("daemon_stopped",False)
    logger.info(f"\n{'='*55}\nCell: {cn} N={N} W={W} B={B} M={M}\n{'='*55}")
    abort_check()
    sched=make_schedule(modes,rc,seed=RANDOM_SEED); tot_errs=0
    for mode,rep,wu in sched:
        bid=f"linux_{cn}_{mode}_rep{rep}{'_warmup' if wu else ''}"
        if bid in progress: logger.info(f"[Skip] {bid}"); continue
        abort_check()
        if ds: pm.stop_daemon()
        else:
            img="patchwork-django:latest" if wl in("django","mixed") else "patchwork-express:latest"
            pm.wait_steady(img,B,M,N,ci=2.0)
        logger.info(f"\n[Run] {bid} mode={mode} N={N} wu={wu}")
        sampler=LinuxSampler(burst_id=bid)
        br=await runner.run_burst(experiment=exp,cell=cn,burst_id=bid,mode=mode,workload=wl,
                                  N=N,W=W,B=B,M=M,repetition=rep,is_warmup=wu,receiver=receiver,sampler=sampler)
        pm.record_burst_end(); progress[bid]=True; save_progress(progress); tot_errs+=br.get("count_failed",0)
    return tot_errs


# ============================================================
# ENVIRONMENT CAPTURE
# ============================================================
def capture_env():
    env={}
    u=platform.uname()
    env["uname"]={"system":u.system,"node":u.node,"release":u.release,"version":u.version,"machine":u.machine}
    try: pv=open("/proc/version").read().strip()
    except: pv="unavailable"
    env["proc_version"]=pv; env["is_wsl"]="microsoft" in pv.lower() or "wsl" in pv.lower()
    try: r=subprocess.run(["systemd-detect-virt"],capture_output=True,text=True,timeout=3); env["virt"]=r.stdout.strip()
    except: env["virt"]="none"
    cpu={}
    with open("/proc/cpuinfo") as f: cl=f.readlines()
    for l in cl:
        if "model name" in l: cpu["model"]=l.split(":")[1].strip(); break
    cpu["nproc"]=os.cpu_count()
    cpu["physical_cores"]=len(set(l.split(":")[1].strip() for l in cl if "core id" in l)) or cpu["nproc"]
    try: cpu["governor"]=open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read().strip()
    except: cpu["governor"]="unknown"
    freqs=[]
    for i in range(cpu["nproc"]):
        p=f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq"
        if os.path.exists(p):
            try: freqs.append(int(open(p).read().strip())/1000)
            except: pass
    cpu["cur_freq_mhz"]=freqs if freqs else "unavailable"
    env["cpu"]=cpu
    with open("/proc/meminfo") as f:
        mi=dict(l.split(":")[:2] for l in f if ":" in l)
    env["memory"]={"MemTotal_kB":int(mi["MemTotal"].strip().split()[0]),
                   "MemAvailable_kB":int(mi["MemAvailable"].strip().split()[0]),
                   "SwapTotal_kB":int(mi.get("SwapTotal","0 kB").strip().split()[0])}
    import resource
    s,h=resource.getrlimit(resource.RLIMIT_NOFILE); env["ulimit_nofile"]={"soft":s,"hard":h}
    try:
        r=subprocess.run(["docker","info","--format","{{json .}}"],capture_output=True,text=True,timeout=10)
        di=json.loads(r.stdout)
        env["docker"]={"server_version":di.get("ServerVersion"),"storage_driver":di.get("Driver"),
                       "cgroup_driver":di.get("CgroupDriver"),"cgroup_version":di.get("CgroupVersion"),
                       "cpus":di.get("NCPU"),"memory_bytes":di.get("MemTotal"),"type":"native_linux"}
    except Exception as e: env["docker"]={"error":str(e)}
    try:
        r=subprocess.run(["docker","exec","patchwork-redis-1","redis-cli","info","server"],
                         capture_output=True,text=True,timeout=5)
        for l in r.stdout.splitlines():
            if "redis_version" in l: env["redis"]={"version":l.split(":")[1].strip(),"container":"patchwork-redis-1"}; break
    except: env["redis"]={"error":"unavailable"}
    import redis as _r, rq as _rq
    env["python"]={"version":platform.python_version(),"redis_py":_r.__version__,"rq":_rq.__version__,
                   "numpy":np.__version__,"pandas":pd.__version__}
    env["images"]={};env["image_ids"]={}
    for img,cmd in [("patchwork-django:latest",["python3","--version"]),("patchwork-express:latest",["node","--version"])]:
        try:
            r=subprocess.run(["docker","run","--rm",img]+cmd,capture_output=True,text=True,timeout=30)
            env["images"][img]=(r.stdout.strip() or r.stderr.strip())
        except Exception as e: env["images"][img]=str(e)
    for img in ["patchwork-django:latest","patchwork-express:latest"]:
        try:
            r=subprocess.run(["docker","inspect",img,"--format","{{.Id}}"],capture_output=True,text=True,timeout=5)
            env["image_ids"][img]=r.stdout.strip()[:20]
        except: pass
    env["git_sha"]=GIT_SHA
    env["current_branch_sha"]=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,
                                              cwd="/home/basil/Patchwork_Service").stdout.strip()
    env["power"]=[{"name":b,"status":open(f"/sys/class/power_supply/{b}/status").read().strip()}
                  for b in os.listdir("/sys/class/power_supply/")
                  if os.path.exists(f"/sys/class/power_supply/{b}/status")]
    with open(os.path.join(RESULTS_DIR,"environment.json"),"w") as f: json.dump(env,f,indent=2)
    logger.info("Environment captured")
    return env


def cleanup_system(pm):
    logger.info("Performing final system cleanup...")
    try: pm.stop_daemon()
    except: pass
    try: pm.stop_workers()
    except: pass
    try: pm.stop_api()
    except: pass

    # Remove study containers
    for img in ["patchwork-django:latest", "patchwork-express:latest"]:
        try:
            r = subprocess.run(["docker", "ps", "-a", "--filter", f"ancestor={img}", "-q"],
                               capture_output=True, text=True, timeout=5)
            cids = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
            if cids:
                subprocess.run(["docker", "rm", "-f"] + cids, capture_output=True, timeout=10)
        except: pass

    # Clean Redis keys
    try:
        keys = pm.r.keys("pool:patchwork-django:latest:*") + pm.r.keys("pool:patchwork-express:latest:*")
        if keys: pm.r.delete(*keys)
    except: pass

    logger.info("Cleanup complete.")


async def main():
    logger.info("=== Starting Linux Evaluation Suite ===")
    pm = LinuxProcessManager()
    receiver = None
    try:
        from receiver import WebhookReceiver
        receiver = WebhookReceiver(host="127.0.0.1", port=9999)
        await receiver.start()
        logger.info("Receiver started on 127.0.0.1:9999")

        # Capture environment
        capture_env()

        # Write manifest
        manifest = {
            "git_sha": GIT_SHA,
            "random_seed": RANDOM_SEED,
            "start_time_utc": datetime.now(timezone.utc).isoformat(),
            "platform": "native_linux"
        }
        with open(MANIFEST_FILE, "w") as f:
            json.dump(manifest, f, indent=2)

        # Start API
        if not pm.start_api():
            raise RuntimeError("Failed to start API server")

        runner = LinuxRunner()
        progress = load_progress()

        from linux_eval_parts import run_part0, run_part1, run_part2, run_part3, run_part4
        from linux_eval_report import generate_all_artifacts

        # Part 0
        await run_part0(pm, receiver, runner, progress, RESULTS_DIR, GATES_DIR, RAW_DIR)

        # Part 1
        await run_part1(pm, receiver, runner, progress, RAW_DIR)

        # Part 2
        max_stable = await run_part2(pm, receiver, runner, progress, RAW_DIR)
        max_stable_M = max_stable[2] if max_stable else 16

        # Part 3
        await run_part3(pm, receiver, runner, progress, max_stable, RAW_DIR)

        # Part 4
        await run_part4(pm, progress, RESULTS_DIR, RAW_DIR, max_stable_M)

        # Generate all reports, tables, figures
        win_results_dir = os.path.abspath("eval/results")
        generate_all_artifacts(RESULTS_DIR, RAW_DIR, TABLES_DIR, FIGURES_DIR, win_results_dir)

        update_progress_md("Evaluation Complete", "DONE", "All parts completed successfully. Report generated.")
        logger.info("=== Evaluation Run Complete Successfully! ===")

    except Exception as e:
        logger.exception(f"Fatal error during evaluation: {e}")
        update_progress_md("FAILED", "ERROR", str(e))
    finally:
        if receiver:
            try: await receiver.stop()
            except: pass
        cleanup_system(pm)


if __name__ == "__main__":
    asyncio.run(main())


