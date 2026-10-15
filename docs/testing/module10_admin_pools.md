# Module 10 — Admin Pool Management Manual Testing Guide

This guide provides step-by-step verification for the Pool Management API and Admin UI in `patchwork_service`.

---

## Prerequisites & Environment Setup

All commands are run from `d:\Patchwork-service\`.

1. **Redis**: Running on `localhost:6379`.
2. **Docker Daemon**: Running (`docker info`).

### Terminal A — Backend API Server
```powershell
cd d:\Patchwork-service\backend
.\.backend\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

### Terminal B — Pool Daemon
```powershell
cd d:\Patchwork-service\backend\tasks\pool
..\..\..\backend\.backend\Scripts\python.exe pool_daemon.py
```
Observe the startup banner:
```
[Daemon] Dynamic pool management daemon started.
[Daemon] Default target buffer: 3, Hard cap: 10
[Daemon] Supported default images: patchwork-django:latest, patchwork-express:latest
```

### Terminal C — Frontend Vite Dev Server
```powershell
cd d:\Patchwork-service\frontend
npm run dev
```
Open `http://localhost:5173` in your browser and log in as `admin` / `admin`.

---

## Test Cases

### Test Case 1 — Create a pool with max 5 (Ceiling Semantics)

- **Goal:** Verify creating a pool with `max_containers=5` maintains a warm buffer of 3 idle containers, not filling immediately to 5.
- **Steps:**
  1. Click **Manage Pools** in the sidebar.
  2. Click **Create Pool**.
  3. Enter Name: `test-pool-5`.
  4. Select Docker Image: e.g. `patchwork/node-image:9ea63decf7fe` (or any built image from Manage Images).
  5. Enter Max Containers: `5`. Note the helper text: *"Ceiling. The pool keeps a few containers warm and grows to this limit under load."*
  6. Click **Create Pool**.
  7. Wait ~2–4 seconds for the daemon to scale up the idle buffer.
  8. In a terminal, run: `docker ps --filter "name=test-pool-5"` (or by sanitized image tag).
- **Expected result:**
  1. The table row displays: Total: `3`, Free: `3`, Occupied: `0`, Max (Ceiling): `5`, State: `ready`.
  2. Exactly 3 warm containers are running in Docker, **not 5**.
- **If it fails, what to check:**
  - Check Terminal B (`pool_daemon.py`) output for `[Daemon] Pool low! Idle: 0/3 (Max: 5). Scaling up by 3...`.
  - Check `redis-cli SCARD pool:<image_tag>:idle` returns 3.

---

### Test Case 2 — Occupy containers (checkout) and verify ceiling cap

- **Goal:** Check out containers and verify the daemon refills the warm buffer up to the ceiling (5), but never exceeds 5. A checkout beyond ceiling times out.
- **Steps:**
  1. Open a Python terminal in `d:\Patchwork-service\backend`:
     ```powershell
     .\.backend\Scripts\python.exe
     ```
  2. Acquire 3 containers in Python:
     ```python
     from tasks.pool.pool_manager import acquire_container
     tag = "<your-pool-image-tag>"
     c1 = acquire_container(tag)
     c2 = acquire_container(tag)
     c3 = acquire_container(tag)
     print("Acquired 3 containers:", c1, c2, c3)
     ```
  3. Watch the UI: Free drops to 0, Occupied becomes 3.
  4. Wait 2–4 seconds: Daemon provisions 2 more containers to refill the buffer towards the ceiling.
  5. Observe UI table: Total becomes `5`, Free: `2`, Occupied: `3`.
  6. In Python, acquire the remaining 2 containers:
     ```python
     c4 = acquire_container(tag)
     c5 = acquire_container(tag)
     print("Acquired remaining 2:", c4, c5)
     ```
  7. Observe UI: Total: `5`, Free: `0`, Occupied: `5`. Total never exceeds 5!
  8. Try acquiring a 6th container (beyond the ceiling):
     ```python
     acquire_container(tag, timeout_seconds=5)
     ```
- **Expected result:**
  1. The 6th acquire waits for available slots and raises `TimeoutError: Timeout: No warm <tag> containers became available in time.` after 5 seconds.
  2. Total containers remains strictly capped at `5`.
- **If it fails, what to check:**
  - Check `pool_daemon.py` `scale_up_pool` formula: `available_slots = max(0, max_containers - total_containers)`.

---

### Test Case 3 — Scale max from 5 to 8 while containers are busy

- **Goal:** Verify scaling up the ceiling while containers are busy immediately allows the daemon to provision additional buffer up to the new ceiling.
- **Steps:**
  1. While `c1` through `c5` are still occupied from Test Case 2 (Total: 5, Occupied: 5, Free: 0).
  2. On the Manage Pools page, click **Scale** on `test-pool-5`.
  3. Change Max Containers from `5` to `8`.
  4. Click **Save Scaling**.
  5. Wait 2–4 seconds.
- **Expected result:**
  1. The daemon detects `needed = 3 - 0 = 3` and `available_slots = 8 - 5 = 3`.
  2. Daemon provisions 3 new warm idle containers.
  3. The UI table updates in real time without refreshing:
     - Max (Ceiling): `8`
     - Total: `8`
     - Free: `3`
     - Occupied: `5`
     - State: `ready`
- **If it fails, what to check:**
  - Check Terminal B daemon logs for `[Daemon] Pool low! Idle: 0/3 (Max: 8). Scaling up by 3...`.

---

### Test Case 4 — Scale down with idle only (Max 8 -> 1)

- **Goal:** Verify that lowering the ceiling when containers are idle removes excess idle containers until total <= max.
- **Steps:**
  1. In your Python terminal from Test Case 2/3, release all containers:
     ```python
     from tasks.pool.pool_manager import release_container
     for cid in [c1, c2, c3, c4, c5]:
         release_container(tag, cid)
     ```
  2. UI table now shows: Total: `8`, Free: `8`, Occupied: `0`.
  3. Click **Scale** on `test-pool-5`.
  4. Change Max Containers from `8` to `1`.
  5. Click **Save Scaling**.
  6. Wait 2–4 seconds.
- **Expected result:**
  1. The daemon triggers the ceiling scale-down rule: `total (8) > max (1)`.
  2. Reaps 7 idle containers.
  3. UI updates in real time to: Total: `1`, Free: `1`, Occupied: `0`, Max: `1`.
  4. In terminal, `docker ps` confirms only 1 container remains running for this pool.
- **If it fails, what to check:**
  - Check `scale_down_pool` ceiling check in `pool_daemon.py`: `excess_total = total_containers - max_containers`.

---

### Test Case 5 — Scale down while busy (Draining Semantics)

- **Goal:** Verify lowering ceiling while containers are busy does NOT kill busy containers; shows draining note in UI and reaps containers as they are released.
- **Steps:**
  1. Scale `test-pool-5` back to Max `5`. (Total becomes 3 idle).
  2. In Python terminal, acquire 2 containers (`b1 = acquire_container(tag)`, `b2 = acquire_container(tag)`).
     - UI shows: Total: 3 (1 free, 2 occupied).
  3. In UI, click **Scale** and lower Max Containers to `1`.
  4. Observe UI state:
     - The daemon reaps the 1 free container.
     - Busy containers `b1` and `b2` are **NOT** killed.
     - Total is `2`, Free is `0`, Occupied is `2`.
     - State badge displays: `draining to 1…`.
  5. In Python, release `b1`:
     ```python
     release_container(tag, b1)
     ```
  6. Within 2 seconds, daemon detects `total (2) > max (1)` with 1 idle container, and immediately reaps `b1`.
     - Total becomes `1`, Occupied `1`, Free `0`.
  7. In Python, release `b2`:
     ```python
     release_container(tag, b2)
     ```
  8. `b2` returns to idle. Total is now 1 <= Max 1. The container is kept warm as the 1 allowed container!
  9. State badge changes from `draining…` to `ready`.
- **Expected result:**
  1. Zero busy containers were killed during or after scale-down.
  2. Excess containers were reaped cleanly on release until reaching the target ceiling.
- **If it fails, what to check:**
  - Verify `scale_down_pool` never touches `_get_busy_key(image_tag)`.

---

### Test Case 6 — Max 0 (Drained Pool) and Scale Back Up

- **Goal:** Verify setting `max_containers=0` completely drains the pool to 0 containers, and scaling back up refills the warm buffer.
- **Steps:**
  1. Click **Scale** on `test-pool-5`.
  2. Set Max Containers: `0`. Click **Save Scaling**.
  3. Wait 2–4 seconds.
- **Expected result:**
  1. Any remaining idle containers are killed.
  2. UI table shows: Total: `0`, Free: `0`, Occupied: `0`, Max: `0`, State: `drained (0)`.
  3. Run `docker ps`: no containers for this tag exist.
  4. Click **Scale**, change Max Containers to `3`.
  5. Wait 2–4 seconds: Daemon provisions 3 warm containers. State becomes `ready`.
- **If it fails, what to check:**
  - Check `scale_up_pool` handles `max_containers=0` (`target_idle = min(3, 0) = 0`).

---

### Test Case 7 — Default Pools (Django & Express) Maintained & Non-deletable

- **Goal:** Verify that `patchwork-django` and `patchwork-express` default pools are automatically seeded, visible with `default` badge, scalable, and cannot be deleted.
- **Steps:**
  1. On the Manage Pools page, locate `patchwork-django` and `patchwork-express`.
  2. Verify both have a gray `default` badge next to their names.
  3. Verify their **Delete** button is disabled or not present.
  4. Click **Scale** on `patchwork-django`, change Max from `10` to `4`.
  5. Click **Save Scaling**: verify ceiling changes to 4.
  6. In browser DevTools Console, try sending a delete request:
     ```js
     fetch('/api/admin/pools/patchwork-django', { method: 'DELETE', credentials: 'same-origin' })
       .then(r => r.json()).then(console.log);
     ```
  7. Try creating a new pool with Image Tag `patchwork-django:latest`.
- **Expected result:**
  1. Delete request returns HTTP 400: `{"detail": "Cannot delete default system pool 'patchwork-django'."}`.
  2. Creating a pool with `patchwork-django:latest` returns HTTP 409 Conflict: `{"detail": "A pool for image tag 'patchwork-django:latest' already exists."}`.
  3. Daemon continues to maintain warm containers for default pools alongside user-created pools.
- **If it fails, what to check:**
  - Check `delete_pool` in `backend/routes/admin_pools.py` checks `pool.get("is_default")`.

---

### Test Case 8 — Delete Pool (409 while busy, clean delete when idle)

- **Goal:** Verify deleting a pool is rejected with 409 if any containers are occupied, and cleanly removes containers and Redis keys when idle.
- **Steps:**
  1. On `test-pool-5`, acquire a container in Python terminal: `c = acquire_container(tag)`.
  2. In UI, click **Delete** on `test-pool-5`.
  3. In the confirmation modal, click **Delete Pool**.
  4. Observe error message: `Cannot delete pool 'test-pool-5' while 1 container(s) are occupied.`
  5. The pool is NOT deleted.
  6. In Python terminal, release the container: `release_container(tag, c)`.
  7. In UI, click **Delete** and confirm.
- **Expected result:**
  1. Deletion succeeds.
  2. `test-pool-5` row disappears from the table.
  3. In terminal, all idle containers are killed.
  4. Redis keys `pool:<tag>:idle` and `pool:<tag>:busy` are deleted.
- **If it fails, what to check:**
  - Check `DELETE /api/admin/pools/{name}` in `admin_pools.py`.

---

### Test Case 9 — Restart the daemon: pools persist and refill

- **Goal:** Verify pool configurations persist in Redis and the daemon resumes managing them after restart.
- **Steps:**
  1. Create a pool `persist-pool` with Max `4` for a built image.
  2. In Terminal B, press `Ctrl+C` to shut down `pool_daemon.py`.
  3. Notice the daemon kills running containers on shutdown.
  4. Restart `pool_daemon.py`:
     ```powershell
     ..\..\..\backend\.backend\Scripts\python.exe pool_daemon.py
     ```
  5. Check UI at `http://localhost:5173/pools`.
- **Expected result:**
  1. `persist-pool` is still present in the list with Max `4`.
  2. The daemon picks up `persist-pool` from Redis and provisions 3 warm containers within 2–4 seconds.
- **If it fails, what to check:**
  - Check `list_pool_defs` in `storage/pool_defs.py`.

---

### Test Case 10 — Corrupt a pool record on purpose (Daemon Resilience)

- **Goal:** Verify that a corrupt or malformed pool record in Redis is logged and skipped without crashing the daemon loop or affecting other pools.
- **Steps:**
  1. In a terminal, write a corrupt record directly into Redis:
     ```powershell
     # Using python to set a corrupt pool hash with missing image_tag and invalid max_containers
     .\.backend\Scripts\python.exe -c "import redis; r = redis.Redis(host='localhost', port=6379); r.hset('pool_config:corrupt-pool', mapping={'name': 'corrupt-pool', 'max_containers': 'invalid_num'}); r.sadd('pool_configs', 'corrupt-pool')"
     ```
  2. Watch Terminal B (`pool_daemon.py`) console output.
- **Expected result:**
  1. Daemon prints a warning: `[Daemon] Skipping corrupt pool record 'corrupt-pool': invalid max_containers 'invalid_num'` (or similar).
  2. The daemon does **NOT** crash.
  3. Other pools (`patchwork-django`, `patchwork-express`, and user pools) continue to be scaled and managed normally.
  4. Clean up the corrupt test record:
     ```powershell
     .\.backend\Scripts\python.exe -c "import redis; r = redis.Redis(host='localhost', port=6379); r.delete('pool_config:corrupt-pool'); r.srem('pool_configs', 'corrupt-pool')"
     ```
- **If it fails, what to check:**
  - Verify `pool_daemon.py` lines 180–210 wrap each pool in `try/except` with explicit field validation.

---

### Test Case 11 — Validation & Edge Cases

- **Goal:** Verify input validation for pool creation and scaling.
- **Steps:**
  1. Click **Create Pool**.
  2. Name validation: Try entering `invalid name with spaces!`.
     - Client validation flags: *Pool name must contain only alphanumeric characters, hyphens, and underscores.*
  3. Max containers range: Try entering `15` or `-2`.
     - Flags: *Max containers must be between 0 and 10.*
  4. Image not found in Docker: Try sending a POST request with an unbuilt/non-existent image tag:
     ```js
     fetch('/api/admin/pools', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       credentials: 'same-origin',
       body: JSON.stringify({ name: 'fake-pool', image_tag: 'nonexistent:latest', max_containers: 3 })
     }).then(r => r.json()).then(console.log);
     ```
- **Expected result:**
  - API returns HTTP 400 Bad Request: `Image tag 'nonexistent:latest' does not exist in Docker daemon.`
