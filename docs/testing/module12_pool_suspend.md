# Module 12 — Pool Suspend & Activate Manual Testing Guide

This guide provides complete, step-by-step verification for suspending and activating container pools in `patchwork_service`.

---

## Prerequisites & Environment Setup

All commands are run from `d:\Patchwork-service\`.

1. **Redis**: Running on `localhost:6379`.
2. **Docker Daemon**: Active and running (`docker info`).

### Terminal 1 — Backend API Server
```powershell
cd d:\Patchwork-service\backend
.\.backend\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

### Terminal 2 — Dynamic Pool Daemon
```powershell
cd d:\Patchwork-service\backend\tasks\pool
..\..\..\backend\.backend\Scripts\python.exe pool_daemon.py
```

### Terminal 3 — RQ Worker
```powershell
cd d:\Patchwork-service\backend\worker
..\.backend\Scripts\python.exe run_worker.py
```

### Terminal 4 — Frontend Vite Dev Server
```powershell
cd d:\Patchwork-service\frontend
npm run dev
```

Open `http://localhost:5173` in your browser and log in as `admin` / `admin`.

---

## Test Cases

### Test Case 1 — Suspend an idle pool

- **Goal:** Verify suspending an idle pool drains and terminates all idle containers while preserving configured ceiling (`max_containers`) and pool definition.
- **Steps:**
  1. Open **Manage Pools** (`/pools`).
  2. Locate an active pool (e.g. `test-pool` or `default-django`). Observe: State is `ready`, Total is `3`, Free is `3`.
  3. Click the **Suspend** button in the Actions column.
  4. In the confirmation modal, review the explanation:
     - *"Idle containers are removed immediately."*
     - *"Busy containers finish their current job and are then removed."*
     - *"New submissions for this image will not be served until it is activated."*
  5. Click **Suspend Pool**.
  6. In a terminal, run:
     ```powershell
     docker ps --filter "name=patchwork"
     ```
  7. Check Redis:
     ```powershell
     python -c "import redis; r=redis.Redis(host='localhost', port=6379, decode_responses=True); print('idle set:', r.smembers('pool:patchwork-django:latest:idle'))"
     ```
- **Expected result:**
  1. The API responds with HTTP 200 and status `"suspended"`.
  2. Within 2–4 seconds, the daemon reaps all idle containers in Docker.
  3. Redis idle set becomes empty (`set()`).
  4. The UI table row updates:
     - State badge displays: `Suspended`
     - Total: `0`, Free: `0`, Occupied: `0`
     - Max (Ceiling): unchanged (e.g. `10` or configured value)
     - Action button switches from **Suspend** to **Activate**.
- **If it fails, what to check:**
  - Check Terminal 2 daemon log for `[Daemon] Reaped (ceiling): ...`.
  - Verify `redis_client.hget("pool_config:<name>", "status")` returns `"suspended"`.

---

### Test Case 2 — Daemon does not recreate containers while suspended

- **Goal:** Verify that the daemon respects suspended status and does not replenish warm containers over time.
- **Steps:**
  1. Keep the pool suspended from Test Case 1.
  2. Watch the pool row in the UI and the Terminal 2 daemon log for at least 15–30 seconds.
  3. Run `docker ps` periodically.
- **Expected result:**
  1. Zero containers are created by the daemon.
  2. No scale-up logs appear in Terminal 2.
  3. Total and Free remain strictly at `0`.
- **If it fails, what to check:**
  - Verify `pool_daemon.py` checks `if status == "suspended":` and skips `scale_up_pool`.

---

### Test Case 3 — Activate: warm buffer refills to ready state

- **Goal:** Verify activating a suspended pool restores container provisioning up to the warm buffer (`min(TARGET_BUFFER, max_containers)`).
- **Steps:**
  1. On the suspended pool from Test Case 2, click **Activate** (no confirmation required).
  2. Watch the UI and Terminal 2 daemon logs.
  3. Run `docker ps`.
- **Expected result:**
  1. API responds with HTTP 200 and status `"active"`.
  2. On the next daemon tick (within 2 seconds), the daemon logs:
     `[Daemon][<tag>] Pool low! Idle: 0/3 (Max: <max>). Scaling up by 3...`
  3. 3 new warm containers appear in `docker ps`.
  4. UI table updates:
     - State badge becomes `ready`
     - Total: `3`, Free: `3`
     - Button switches back to **Suspend**.
- **If it fails, what to check:**
  - Verify `pool_defs.py` updated `"status"` to `"active"` in Redis hash.

---

### Test Case 4 — Suspend while a container is busy (Draining behavior)

- **Goal:** Verify that suspending a pool while containers are occupied running jobs does not kill busy containers, transitions state to `Draining`, and reaps them only upon release.
- **Steps:**
  1. Open a Python terminal in `d:\Patchwork-service\backend`:
     ```powershell
     .\.backend\Scripts\python.exe
     ```
  2. Check out a container:
     ```python
     from tasks.pool.pool_manager import acquire_container
     tag = "patchwork-django:latest" # or your pool's image tag
     cid = acquire_container(tag)
     print("Checked out busy container:", cid)
     ```
  3. In the UI, observe: Occupied is `1`, Free is `2`, Total is `3`.
  4. Click **Suspend** on this pool and confirm.
  5. Check `docker ps`:
     - The 2 idle containers are killed.
     - The busy container (`cid`) is **still alive** and running!
  6. UI table updates:
     - Total: `1`, Free: `0`, Occupied: `1`
     - State badge displays: **`Draining`**
  7. In your Python terminal, release the container (simulating job completion):
     ```python
     from tasks.pool.pool_manager import release_container
     release_container(tag, cid)
     ```
  8. Watch the daemon log and UI for 2–4 seconds.
- **Expected result:**
  1. When released, the container enters the idle set.
  2. On the next daemon tick, the daemon drains it immediately via `scale_down_pool(max_containers=0)`.
  3. The container is killed in Docker.
  4. UI updates: Total becomes `0`, Occupied becomes `0`, and State badge transitions from `Draining` to `Suspended`.
- **If it fails, what to check:**
  - Verify `scale_down_pool` reaps idle containers using `spop` and does not touch `busy_key`.

---

### Test Case 5 — Submit work to a suspended pool: Fast-fail verification

- **Goal:** Verify that submissions targeting an image whose pool is suspended fail immediately with a clear error without waiting out the 10-second acquire timeout.
- **Steps:**
  1. Ensure the pool (e.g. `default-django`, tag `patchwork-django:latest`) is suspended.
  2. Open a Python terminal in `d:\Patchwork-service\backend`:
     ```powershell
     .\.backend\Scripts\python.exe
     ```
  3. Run:
     ```python
     import time
     from tasks.pool.pool_manager import acquire_container, PoolSuspendedError

     start = time.time()
     try:
         acquire_container("patchwork-django:latest", timeout_seconds=10)
     except PoolSuspendedError as e:
         elapsed = time.time() - start
         print(f"Caught PoolSuspendedError in {elapsed:.3f}s: {e}")
     ```
- **Expected result:**
  1. `PoolSuspendedError` is raised in **< 0.05 seconds** (immediately, NOT after 10 seconds).
  2. Message: `Pool for image 'patchwork-django:latest' is suspended.`
- **If it fails, what to check:**
  - Verify `is_pool_suspended(image_tag)` correctly returns `True` and `acquire_container` raises `PoolSuspendedError` at its start.

---

### Test Case 6 — Scale a suspended pool, then activate: new ceiling applies

- **Goal:** Verify that modifying max containers (ceiling) on a suspended pool persists without activating it prematurely, and the new ceiling takes effect upon activation.
- **Steps:**
  1. Select a suspended pool (e.g. `test-pool` with max 5).
  2. Click **Scale**.
  3. Note helper message: *"Pool is suspended. The new max takes effect when it is activated."*
  4. Change Max Containers to `8`.
  5. Click **Save Scaling**.
  6. Check the pool row:
     - Max (Ceiling): `8`
     - State: still `Suspended`
     - Total: `0`
  7. Now click **Activate**.
  8. Wait ~2–4 seconds for daemon scaling.
- **Expected result:**
  1. Pool activates cleanly.
  2. Daemon provisions up to the buffer `min(3, 8) = 3`.
  3. Under load, total can now scale up to the new ceiling of `8`.
- **If it fails, what to check:**
  - Verify `scale_pool` endpoint does not overwrite the `"status"` field in Redis.

---

### Test Case 7 — Suspend default pool (django / express)

- **Goal:** Verify that default system pools can be suspended and activated, showing the prominent warning in the confirmation modal.
- **Steps:**
  1. In **Manage Pools**, locate `default-django` or `default-express`.
  2. Click **Suspend**.
  3. Verify the modal displays the highlighted amber warning:
     *"Ticket submissions that use this image will fail while it is suspended."*
  4. Confirm suspension.
  5. Click **Activate**.
- **Expected result:**
  1. The default pool suspends cleanly (idle containers drained).
  2. Clicking **Activate** brings the warm buffer back to `3` containers in Docker.
  3. Default pools cannot be deleted, but suspend/activate is fully supported.
- **If it fails, what to check:**
  - Verify `suspendTarget.is_default` triggers the warning display in `PoolList.jsx`.

---

### Test Case 8 — Idempotency of suspend and activate

- **Goal:** Verify that calling suspend on an already-suspended pool or activate on an active pool returns HTTP 200 without error or duplicate operations.
- **Steps:**
  1. In PowerShell, issue duplicate curl requests:
     ```powershell
     # Suspend twice
     curl.exe -X POST "http://localhost:8000/api/admin/pools/default-django/suspend" -H "Cookie: session=<admin-cookie>"
     curl.exe -X POST "http://localhost:8000/api/admin/pools/default-django/suspend" -H "Cookie: session=<admin-cookie>"

     # Activate twice
     curl.exe -X POST "http://localhost:8000/api/admin/pools/default-django/activate" -H "Cookie: session=<admin-cookie>"
     curl.exe -X POST "http://localhost:8000/api/admin/pools/default-django/activate" -H "Cookie: session=<admin-cookie>"
     ```
- **Expected result:**
  1. All calls return HTTP 200 with the pool object.
  2. No 500 errors, no state corruption.
- **If it fails, what to check:**
  - Verify `suspend_pool` and `activate_pool` check `if pool.get("status") == ...:` and return early.

---

### Test Case 9 — Explicit restart test: API and daemon restarts

- **Goal:** Verify that a suspended default pool remains suspended across restarts of the API server, the pool daemon, and both together, and that seed code never resets its status to active.
- **Steps:**
  1. Suspend `default-django`. Verify in UI and Redis that status is `"suspended"` and Total is `0`.
  2. **Restart API Server**: Stop Terminal 1 (`Ctrl+C`) and restart `python -m uvicorn main:app --reload`.
  3. Reload browser page `/pools`. Check `default-django`:
     - Must still be `Suspended`.
     - Zero containers in `docker ps`.
  4. **Restart Pool Daemon**: Stop Terminal 2 (`Ctrl+C`) and restart `python pool_daemon.py`.
  5. Observe daemon startup logs:
     - `seed_default_pools()` executes.
     - Observe: `default-django` is NOT provisioned. No warm containers spawned.
  6. **Restart Both Together**: Stop Terminal 1 and Terminal 2, then start both.
  7. Check `docker ps` after 10 seconds.
- **Expected result:**
  1. `default-django` remains strictly suspended across all restarts.
  2. `seed_default_pools` only initializes missing keys and NEVER overwrites existing records.
- **If it fails, what to check:**
  - Check `seed_default_pools` in `backend/storage/pool_defs.py`: verify it has `if not redis_client.exists(key):` before calling `hset`.

---

### Test Case 10 — Existing pool records with no status field (Backwards Compatibility)

- **Goal:** Verify that if an existing pool record in Redis lacks the `status` field, it seamlessly defaults to `"active"`.
- **Steps:**
  1. In a Python terminal, remove the `status` field from a pool record in Redis:
     ```powershell
     python -c "import redis; r=redis.Redis(host='localhost', port=6379, decode_responses=True); r.hdel('pool_config:default-django', 'status'); print('Deleted status field')"
     ```
  2. In Terminal 2, observe the daemon loop.
  3. Refresh the pools page in the UI.
- **Expected result:**
  1. `_format_pool_data` interprets missing `status` as `"active"`.
  2. The daemon treats the pool as active and maintains its warm buffer.
  3. UI displays the pool as `ready` with its normal `Suspend` button.
- **If it fails, what to check:**
  - Verify `_format_pool_data` in `pool_defs.py`: `status = "active" if raw_status is None else raw_status`.

---

### Test Case 11 — Delete a suspended pool

- **Goal:** Verify that a suspended pool with 0 busy containers can be deleted, while one with busy containers returns HTTP 409 Conflict.
- **Steps:**
  1. Create a custom pool `test-del-pool` with an image.
  2. Suspend `test-del-pool`. Wait until Total is `0`.
  3. Click **Delete** and confirm.
  4. Verify: pool definition is removed cleanly from Redis (`POOLS_SET_KEY`).
  5. Check deletion when busy:
     - Create another pool `test-busy-pool`.
     - Acquire a container in Python to make it busy.
     - Suspend the pool (it enters `Draining`).
     - Attempt to delete it via API:
       `DELETE /api/admin/pools/test-busy-pool`
- **Expected result:**
  1. Deleting an idle suspended pool succeeds (HTTP 204).
  2. Deleting a draining suspended pool with busy containers fails with HTTP 409 Conflict.
- **If it fails, what to check:**
  - Verify `delete_pool` in `admin_pools.py` checks `occupied > 0` and raises 409.

---

### Test Case 12 — Session expiry redirects to login

- **Goal:** Verify that calling suspend or activate after session expiration redirects to login.
- **Steps:**
  1. On `/pools`, open DevTools -> Application -> Cookies.
  2. Delete the `session` cookie.
  3. Click **Suspend** or **Activate** on any pool.
- **Expected result:**
  1. Request receives HTTP 401 Unauthorized.
  2. App redirects to `/login`.
- **If it fails, what to check:**
  - Verify `apiFetch` in `api.js` dispatches `auth:expired` on HTTP 401.

---

### Test Case 13 — Waiting submission fails fast when pool is suspended mid-wait

- **Goal:** Verify that a submission already waiting in `acquire_container`'s loop fails fast as soon as the pool is suspended.
- **Steps:**
  1. In a Python terminal, exhaust all containers of an active pool by acquiring them so the pool has 0 idle containers.
  2. In a second Python terminal, initiate an acquire request with a 15-second timeout in a background thread:
     ```python
     import time, threading
     from tasks.pool.pool_manager import acquire_container, PoolSuspendedError

     def try_acquire():
         t0 = time.time()
         try:
             acquire_container("patchwork-django:latest", timeout_seconds=15)
         except PoolSuspendedError as e:
             print(f"\n[Worker] Successfully caught PoolSuspendedError after {time.time() - t0:.2f}s: {e}")

     t = threading.Thread(target=try_acquire)
     t.start()
     ```
  3. 2 seconds into the wait, suspend `default-django` via the UI or API:
     ```powershell
     curl.exe -X POST "http://localhost:8000/api/admin/pools/default-django/suspend" -H "Cookie: session=<admin-cookie>"
     ```
- **Expected result:**
  1. The waiting thread detects the suspended status on its next loop iteration (within ~0.1s).
  2. `PoolSuspendedError` is raised immediately (~2.1s total, far before the 15-second timeout).
- **If it fails, what to check:**
  - Verify `acquire_container` calls `if is_pool_suspended(image_tag): raise PoolSuspendedError(...)` inside the `while` loop.

---

### Test Case 14 — Failed submission is not retried and webhook receives error payload

- **Goal:** Verify that when a submission fails due to a suspended pool, RQ does not retry the job and the webhook receives the failure payload.
- **Steps:**
  1. Suspend `default-django`.
  2. Submit an execution ticket via POST `/v1/execute` with an idempotency key and callback URL (e.g. `http://httpbin.org/post` or local mock server).
  3. Observe Terminal 3 (RQ Worker).
  4. Query `/v1/status/{job_id}`:
     ```powershell
     curl.exe "http://localhost:8000/v1/status/<job_id>" -H "X-Client-ID: <client-id>" -H "X-Client-Secret: <secret>"
     ```
- **Expected result:**
  1. The worker processes the job and catches `PoolSuspendedError`.
  2. Status returns `"failed"`.
  3. `stderr` contains: `Failed to acquire container: Pool for image 'patchwork-django:latest' is suspended.`
  4. The job is marked finished (not retried in RQ).
- **If it fails, what to check:**
  - Verify `tasks.py` catches `PoolSuspendedError` and returns `{"error": ...}` without raising an unhandled exception to RQ.

---

### Test Case 15 — PATCH max_containers on suspended pool preserves fields in Redis

- **Goal:** Verify that modifying `max_containers` on a suspended pool preserves `status: "suspended"`, `is_default`, and `created_at` in Redis.
- **Steps:**
  1. Ensure `default-django` is suspended.
  2. In Python, inspect the Redis hash before patching:
     ```python
     import redis
     r = redis.Redis(host='localhost', port=6379, decode_responses=True)
     print("Before:", r.hgetall("pool_config:default-django"))
     ```
  3. In the UI or via PATCH `/api/admin/pools/default-django`, change `max_containers` to `7`.
  4. Inspect the Redis hash again:
     ```python
     print("After:", r.hgetall("pool_config:default-django"))
     ```
- **Expected result:**
  1. `max_containers` is `"7"`.
  2. `status` remains `"suspended"`.
  3. `is_default` remains `"1"`.
  4. `created_at` remains unchanged.
  5. `updated_at` reflects the new timestamp.
- **If it fails, what to check:**
  - Verify `update_pool_def` in `pool_defs.py` uses field-level `hset(mapping=...)` and does not overwrite unmentioned fields.
