# Module 7 — Async Image-Build Testing Guide

Status values used by the build pipeline (exact strings stored in JSON):

| Value | Set by |
|---|---|
| `queued` | `trigger_image_build` handler, immediately on enqueue |
| `building` | `run_image_build` worker, before Docker SDK call |
| `success` | `run_image_build` worker, on successful Docker build |
| `failed` | `run_image_build` worker, on Docker error OR caught `BaseException` |

---

## Prerequisites

All terminals share the same `backend/` working directory unless noted.

```
cd d:\Patchwork-service\backend
```

Redis must be running (default `localhost:6379`).  
Docker daemon must be running.

### Terminal A — API server

```powershell
python -m uvicorn main:app --reload --port 8000
```

### Terminal B — RQ worker

```powershell
python worker\run_worker.py
```

The worker listens on **both** queues:

```
queues=["submission_queue", "image_builds"]
```

### Admin login (cookie jar)

With `AUTH_MODE=none` (default `.env`) the `Authorization` header is not
checked, so all admin endpoints are open.  
If you later enable `AUTH_MODE=api_key`, obtain a token first:

```bash
# One-time login — saves session cookie to jar.txt
curl -s -c jar.txt -X POST http://localhost:8000/api/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<password>"}' | python -m json.tool

# All subsequent admin calls append: -b jar.txt
```

With `AUTH_MODE=none` you can omit `-b jar.txt` entirely.

---

## Test Case 1 — Python happy path

**Goal:** A Python image definition builds successfully; `status` becomes
`success` and `image_tag` is populated.

**Commands**

```bash
# 1. Create definition
curl -s -X POST http://localhost:8000/api/admin/images \
  -H "Content-Type: application/json" \
  -d '{"name":"py-test","runtime":"python"}' | python -m json.tool

# 2. Add a dependency
curl -s -X POST http://localhost:8000/api/admin/images/py-test/dependencies \
  -H "Content-Type: application/json" \
  -d '{"name":"requests","version":"2.32.3"}' | python -m json.tool

# 3. Trigger build
curl -s -X POST http://localhost:8000/api/admin/images/py-test/build \
  | python -m json.tool
# Expected immediately: {"status":"queued", ...}

# 4. Poll until done (repeat every 10 s)
curl -s http://localhost:8000/api/admin/images/py-test | python -m json.tool
```

**Expected result**  
After the worker finishes (typically 1–4 minutes):

```json
{
  "status": "success",
  "image_tag": "patchwork/pytest:<12-char-hash>",
  "build_log": "Step 1/9 : FROM python:3.12.11-slim-bookworm\n..."
}
```

**If it fails, check**
- Worker terminal for Docker SDK error output.
- `docker images | grep patchwork` — image may already exist (content-hash match → skip).
- `docker info` — daemon reachable from the worker process.

---

## Test Case 2 — Node happy path

**Goal:** A Node image definition builds successfully.

**Commands**

```bash
curl -s -X POST http://localhost:8000/api/admin/images \
  -H "Content-Type: application/json" \
  -d '{"name":"node-test","runtime":"node"}' | python -m json.tool

curl -s -X POST http://localhost:8000/api/admin/images/node-test/dependencies \
  -H "Content-Type: application/json" \
  -d '{"name":"lodash","version":"4.17.21"}' | python -m json.tool

curl -s -X POST http://localhost:8000/api/admin/images/node-test/build \
  | python -m json.tool
# Expected immediately: {"status":"queued", ...}

# Poll
curl -s http://localhost:8000/api/admin/images/node-test | python -m json.tool
```

**Expected result**  
`status: "success"`, `image_tag` set, `build_log` contains npm install output.

**If it fails, check**
- npm registry reachable from the Docker build network.
- Worker logs for `BuildError`.

---

## Test Case 3 — Non-blocking proof

**Goal:** The POST `/build` handler returns before the Docker build finishes.

**Commands**

```bash
# Time the trigger call — it must return in well under 1 second
Measure-Command {
  curl -s -X POST http://localhost:8000/api/admin/images/py-test/build
}
```

**Expected result**  
`TotalSeconds` ≪ 1 (typically < 0.05 s).  
The handler sets `status: "queued"` and returns; the worker is doing the
blocking Docker work in its own process.

**If it fails, check**
- Confirm the handler in `routes/admin_images.py` calls only
  `update_image_def` and `image_builds_queue.enqueue` — no Docker SDK import
  or call anywhere in the handler body.
- Check that no `import docker` or `build_image` call is at module level in
  `admin_images.py`.

---

## Test Case 4 — Duplicate trigger → HTTP 409

**Goal:** Triggering a build while one is already `queued` or `building`
returns HTTP 409.

**Commands**

```bash
# Trigger first build
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/admin/images/py-test/build
# Expected: 200 (first trigger, definition was in draft/success/failed)

# Trigger immediately again before worker picks it up
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/admin/images/py-test/build
# Expected: 409

# Full body
curl -s -X POST http://localhost:8000/api/admin/images/py-test/build \
  | python -m json.tool
# Expected body:
# {"detail": "Image definition 'py-test' is already queued"}
```

**Expected result**  
HTTP `409 Conflict`.  
Detail string: `"Image definition 'py-test' is already queued"` or
`"...already building"` depending on race timing.

**If it fails, check**  
`routes/admin_images.py` lines ~205–209: guard checks
`image_def.get("status") in ("building", "queued")`.

---

## Test Case 5 — Forced failure → `failed` with error in `build_log`

**Goal:** A build that cannot succeed writes `status: "failed"` and a
readable error into `build_log`.

**Method A — invalid Dockerfile via monkey-patch (simplest)**

Temporarily edit `services/image_build.py` to replace the `FROM` line with
`FROM this-image-does-not-exist:never` in `PYTHON_BASELINE_DOCKERFILE`, then
restore it after the test.

```bash
# After editing, trigger
curl -s -X POST http://localhost:8000/api/admin/images/py-test/build \
  | python -m json.tool

# Poll
curl -s http://localhost:8000/api/admin/images/py-test | python -m json.tool
```

**Method B — kill Docker daemon mid-build**

1. Start a build of a definition with several dependencies (slow build).
2. While the worker is in `building` state, stop the Docker daemon.
3. Poll the definition — after the worker's exception handler fires,
   `status` must be `"failed"` and `build_log` must contain the error string.

**Expected result**  
```json
{
  "status": "failed",
  "build_log": "<Docker error message or 'Error during Connect...'>"
}
```

**If it fails, check**  
- `worker/build_job.py`: `except BaseException as exc` block — confirm it
  calls `update_image_def` with `{"status": "failed", "build_log": error_msg}`.
- Worker terminal for traceback (the `raise` at the end re-raises so RQ logs it).

---

## Test Case 6 — Worker offline then restarted (job persists in Redis)

**Goal:** Jobs enqueued while the worker is down are picked up when it comes
back online.

**Commands**

```bash
# Terminal B: stop the worker (Ctrl-C)

# Enqueue a build — it is stored in Redis, no worker processes it yet
curl -s -X POST http://localhost:8000/api/admin/images/py-test/build \
  | python -m json.tool
# status: "queued"

# Verify the job is waiting in Redis
redis-cli llen rq:queue:image_builds
# Expected: 1

# Also check the raw key
redis-cli keys "rq:job:*" | head -5

# Restart worker (Terminal B)
python worker\run_worker.py
# Worker immediately dequeues and processes the job.

# Poll until complete
curl -s http://localhost:8000/api/admin/images/py-test | python -m json.tool
```

**Expected result**  
After restart the worker picks up the waiting job;  
`status` transitions `queued → building → success` (or `failed`).

**If it fails, check**  
- `redis-cli ping` — Redis still running.
- `redis-cli llen rq:queue:image_builds` — if 0, job may have been consumed
  by a stale worker process.

---

## Test Case 7 — Worker listens on both queues; ticket submission during a build

**Goal (part A):** Confirm the worker processes both `submission_queue` and
`image_builds`.

```bash
# Check worker startup log (Terminal B):
# Should print something like:
#   Worker rq:worker:... started, version ...
#   Listening on submission_queue, image_builds ...
#
# Or verify directly in run_worker.py:
python -c "
import sys, os
sys.path.insert(0, r'd:\Patchwork-service\backend')
from worker.run_worker import *
# This just imports — look at the queues= list passed to SimpleWorker
"
```

**Goal (part B):** A ticket submitted while a build is running is not delayed
by the build.

> **Important:** `SimpleWorker` is **single-threaded**. A long Docker build
> will block the worker from processing `submission_queue` jobs until the
> build finishes. This is a known limitation of `SimpleWorker` on Windows.
> On Linux, switch to `Worker` (fork-based) for true concurrency between
> queues.

**To measure the wait:**

```bash
# 1. Start a slow build (node definition with many deps)
curl -s -X POST http://localhost:8000/api/admin/images/node-test/build

# 2. Immediately submit a ticket and record timing
$start = Get-Date
curl -s -X POST http://localhost:8000/v1/execute \
  -H "Content-Type: application/json" \
  -H "X-Client-ID: test-client" \
  -d '{
    "idempotency_key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "image_tag": "patchwork/node-test:<hash>",
    "files": [{"filename":"test.js","content":"console.log(1)"}],
    "test_command": "node test.js"
  }' | python -m json.tool
$elapsed = (Get-Date) - $start
Write-Host "Ticket wait: $($elapsed.TotalSeconds) s"
```

**Expected result (with SimpleWorker)**  
The ticket will wait until the current build job finishes.
Record the elapsed time; it will roughly equal the remaining build time.

**Expected result (with fork Worker on Linux)**  
Ticket is dequeued and executed concurrently; wait ≈ 0 s.

---

## Test Case 8 — Cleanup: temp build-context directories removed

**Goal:** No `patchwork_build_*` temp directories linger after a build.

**Commands**

```powershell
# Before triggering a build, note current temp dirs
$before = Get-ChildItem $env:TEMP -Filter "patchwork_build_*" -Directory
Write-Host "Before: $($before.Count)"

# Trigger build
curl -s -X POST http://localhost:8000/api/admin/images/py-test/build

# Wait for completion (poll status until not "building")
Start-Sleep -Seconds 120

# After build
$after = Get-ChildItem $env:TEMP -Filter "patchwork_build_*" -Directory
Write-Host "After: $($after.Count)"
```

**Expected result**  
`After: 0` — `services/image_build.py` uses a `finally` block with
`shutil.rmtree(temp_dir, ignore_errors=True)` that runs on both success and
failure.

**If it fails, check**  
`services/image_build.py` lines ~230–232: `finally: shutil.rmtree(...)`.  
If the process was `SIGKILL`-ed (not `SIGTERM`/`JobTimeoutException`), the
`finally` block may not run. This is an OS-level constraint.

---

## Test Case 9 — Timeout path (simulating a job that exceeds `job_timeout`)

**Goal:** A job that runs longer than `job_timeout=1200 s` is killed by RQ,
and the definition ends up in `failed` (not stuck in `building`).

> **Note:** Waiting 20 real minutes is impractical. Use a minimal `job_timeout`
> override in a scratch script instead.

**Method — custom short timeout enqueue**

```python
# scratch_timeout_test.py  (run from backend/)
import sys, os
sys.path.insert(0, ".")
from redis import Redis
from rq import Queue
from worker.build_job import run_image_build

r = Redis(host="localhost", port=6379)
q = Queue("image_builds", connection=r)

# Enqueue with a 5-second timeout to force a timeout quickly
job = q.enqueue(
    run_image_build,
    "py-test",          # must exist and be in a non-building/queued state
    job_timeout=5,      # intentionally tiny
    result_ttl=3600,
    failure_ttl=3600,
)
print(f"Job {job.id} enqueued with timeout=5s")
```

```powershell
# 1. Make sure py-test is in "draft" or "success" state first
# 2. Run the scratch script
python scratch_timeout_test.py

# 3. Watch worker terminal — you should see JobTimeoutException within 5 s

# 4. Check the definition
curl -s http://localhost:8000/api/admin/images/py-test | python -m json.tool
```

**Expected result**  
```json
{
  "status": "failed",
  "build_log": "JobTimeoutException: ..."
}
```

The `except BaseException` block in `worker/build_job.py` catches the
`rq.timeouts.JobTimeoutException` (a `BaseException` subclass), writes
`status: "failed"` and the exception class + message to `build_log`, then
re-raises so RQ marks the job as failed in Redis.

**If it fails, check**  
- `rq.timeouts.JobTimeoutException` is a `BaseException`, not `Exception`.
  Confirm `worker/build_job.py` uses `except BaseException` (not `except Exception`).
- `status` stuck in `building` → the old code path without the broad catch
  is being used. Verify the file was saved after the fix.

---

## Storage directory resolution (after Step 2 fix)

Both the API server and the worker import `storage.image_defs` at startup.
The module-level `STORAGE_DIR` is resolved **once** using:

```
PATCHWORK_STORAGE_DIR env var   →  used as-is if set
otherwise                       →  <project_root>/saved_docker_images
```

where `<project_root>` is computed as:

```
os.path.dirname(        # → project root  (e.g. d:\Patchwork-service)
    os.path.dirname(    # → backend/
        os.path.abspath(__file__)  # → backend/storage/image_defs.py
    )
)
```

**Result on a Windows checkout at `d:\Patchwork-service`:**

| Process | Resolved path |
|---|---|
| API server | `d:\Patchwork-service\saved_docker_images` |
| RQ worker | `d:\Patchwork-service\saved_docker_images` |

Both processes always agree because the path is derived from the file's
on-disk location, not from CWD.

To override (e.g. in production):

```bash
# Windows PowerShell
$env:PATCHWORK_STORAGE_DIR = "C:\data\patchwork_images"

# Linux / Docker
export PATCHWORK_STORAGE_DIR=/data/patchwork_images
```
