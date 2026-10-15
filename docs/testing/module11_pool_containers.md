# Module 11 — Pool Container Detail & Deletion Testing Guide

This manual testing guide provides end-to-end verification for the Pool Container Detail view (`/pools/:name`) and individual container management (inspection, memory stats, idle deletion, forced busy deletion, race conditions, and missing container cleanup) in `patchwork_service`.

---

## Prerequisites & Environment Setup

All commands are run from `d:\Patchwork-service\`.

1. **Redis**: Running locally on `localhost:6379`.
2. **Docker Daemon**: Active and accessible (`docker info`).

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

### Terminal 3 — Celery Worker (for executing submitted tasks)
```powershell
cd d:\Patchwork-service\backend\worker
..\.backend\Scripts\python.exe run_worker.py
```

### Terminal 4 — Frontend Vite Dev Server
```powershell
cd d:\Patchwork-service\frontend
npm run dev
```

Open `http://localhost:5173` in your browser and log in with your admin credentials (`admin` / `admin`).

---

## Test Cases

### Test Case 1 — Open a pool: container list matches Docker and Redis

- **Goal:** Verify clicking a pool in **Manage Pools** opens `/pools/:name`, displaying every container in that pool with its state matching `docker ps` and Redis sets (`pool:{tag}:idle` / `pool:{tag}:busy`).
- **Steps:**
  1. In the sidebar, click **Manage Pools**.
  2. Click on the pool name link (e.g. `default-django` or a custom pool like `test-pool`).
  3. In a terminal, run:
     ```powershell
     docker ps --filter "name=patchwork"
     ```
  4. In a Python terminal or redis-cli, check Redis keys:
     ```powershell
     docker exec -it <redis-container> redis-cli SMEMBERS pool:patchwork-django:latest:idle
     ```
- **Expected result:**
  1. The browser navigates to `/pools/<name>`.
  2. The table lists every container found in Redis.
  3. Short ID matches the first 12 characters of the Docker container ID.
  4. State displays a green `idle` badge for containers in the idle set.
  5. The container names match the output of `docker ps`.
- **If it fails, what to check:**
  - Verify `GET /api/admin/pools/{name}/containers` returned HTTP 200 in DevTools Network tab.
  - Check that the pool image tag matches what the daemon provisions.

---

### Test Case 2 — Memory values match `docker stats --no-stream`

- **Goal:** Verify reported container memory matches Docker's page-cache-subtracted calculation (`usage - inactive_file`), and the pool total memory equals the sum of the containers.
- **Steps:**
  1. On the pool container detail page, observe the **Memory Usage** column for each running container (e.g. `24.5 MiB / 256 MiB (10%)`).
  2. Note the summary line: **Total Pool Memory** (e.g. `73.5 MiB / 768 MiB`).
  3. In a terminal, run:
     ```powershell
     docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"
     ```
  4. Sum the memory of the individual containers in the pool table.
- **Expected result:**
  1. The memory for each container in the UI matches the `docker stats` memory within ±1 MiB (accounting for live execution drift).
  2. Total Pool Memory in the summary card equals the exact sum of the active containers' memory usage.
- **If it fails, what to check:**
  - Verify that `_inspect_container_info` calculates memory using `max(0, usage - inactive_file)`.

---

### Test Case 3 — Delete an idle container

- **Goal:** Verify deleting an idle container claims it from Redis, kills the Docker container, and lets the daemon replenish the warm buffer.
- **Steps:**
  1. Locate any container with state `idle`.
  2. Note its container name and short ID.
  3. Click **Delete**.
  4. Notice the confirmation modal:
     - Title: `Delete Container`
     - Text: *"Are you sure you want to delete container `<name>`..."*
     - Helper note: *"The pool daemon will automatically provision a new container to maintain the warm buffer."*
  5. Click **Delete Container**.
  6. Watch Terminal 2 (daemon log) and the UI table.
- **Expected result:**
  1. The container disappears immediately from the list.
  2. In `docker ps`, that specific container is terminated.
  3. Within ~2–4 seconds, the pool daemon detects that the idle buffer is below the target and provisions a replacement container.
  4. The UI automatically polls within 5 seconds and shows the new warm container with state `idle`.
- **If it fails, what to check:**
  - Check Terminal 1 API log: verify `DELETE /api/admin/pools/{name}/containers/{id}` returned HTTP 204.
  - Check that Redis `SREM pool:{tag}:idle <name>` succeeded.

---

### Test Case 4 — Delete a busy container without force (409 Conflict)

- **Goal:** Verify that attempting to delete a container currently running a job without `force=true` is rejected with HTTP 409 and does not kill the container.
- **Steps:**
  1. Check out a container using Python to simulate a running job:
     ```powershell
     cd d:\Patchwork-service\backend
     .\.backend\Scripts\python.exe
     ```
     ```python
     from tasks.pool.pool_manager import acquire_container
     tag = "patchwork-django:latest" # or your pool's image tag
     cid = acquire_container(tag)
     print("Checked out container:", cid)
     ```
  2. Refresh or wait 5s on the pool detail page: the container now displays state `busy`.
  3. In another terminal or via curl / Postman, issue a DELETE without `force`:
     ```powershell
     curl -X DELETE "http://localhost:8000/api/admin/pools/default-django/containers/$cid" -H "Cookie: session=<admin-cookie>"
     ```
     *(Or if clicking Delete in the UI, note whether force is disabled or confirmation is requested).*
- **Expected result:**
  1. Server returns HTTP 409 Conflict with message:
     `Container '<cid>' is currently busy running a job. Use force=true to kill it.`
  2. The container remains alive in `docker ps`.
  3. In Python, the acquired container is unaffected.
- **If it fails, what to check:**
  - Verify that `admin_pools.py` checks `target_name in busy_members` and checks `if not force: raise HTTPException(409, ...)`.

---

### Test Case 5 — Force delete a busy container

- **Goal:** Verify force-deleting a busy container kills the container, terminates the execution cleanly without Redis corruption, and leaves no stale entries.
- **Steps:**
  1. On the detail page, locate the busy container from Test Case 4.
  2. Click **Delete**.
  3. The modal recognizes the container is busy:
     - Title: `Force Delete Busy Container?`
     - Prominent yellow warning banner: *"WARNING: Container is currently occupied running a job. Deleting it will kill the container and terminate the running job immediately."*
     - Button labeled: **Force Delete** (red).
  4. Click **Force Delete**.
  5. In your Python terminal from Test Case 4, release the container:
     ```python
     from tasks.pool.pool_manager import release_container
     release_container(tag, cid)
     ```
  6. In Redis, check members of `pool:{tag}:idle` and `pool:{tag}:busy`.
- **Expected result:**
  1. The container is killed immediately and removed from Docker (`docker ps` no longer lists it).
  2. Redis key `pool:{tag}:busy` no longer contains the container.
  3. `release_container()` executes without crashing or resurrecting the dead container into `idle` (because `SMOVE` returns 0).
  4. The pool daemon replenishes an idle container on its next tick to restore the buffer.
- **If it fails, what to check:**
  - Ensure `redis_client.srem(busy_key, target_name)` was called before Docker kill.
  - Verify that `release_container` does not re-add a removed container.

---

### Test Case 6 — Race condition: delete an idle container during checkout

- **Goal:** Verify that if an admin deletes an idle container at the exact moment a worker checks it out, the race is handled safely without double-kill or corruption.
- **Steps:**
  1. Open a Python shell in `d:\Patchwork-service\backend`:
     ```powershell
     .\.backend\Scripts\python.exe
     ```
  2. Find an idle container name from Redis:
     ```python
     import redis
     r = redis.Redis(host="localhost", port=6379, decode_responses=True)
     tag = "patchwork-django:latest"
     idle_c = list(r.smembers(f"pool:{tag}:idle"))[0]
     print("Targeting:", idle_c)
     # Atomically check it out into busy:
     r.smove(f"pool:{tag}:idle", f"pool:{tag}:busy", idle_c)
     ```
  3. Now immediately attempt to delete that container as idle (without force) via API:
     ```powershell
     # Call DELETE /api/admin/pools/default-django/containers/<idle_c>?force=false
     ```
- **Expected result:**
  1. The delete endpoint detects that atomic claim `srem(idle_key)` returned 0, checks `busy_key`, and returns HTTP 409 Conflict:
     `Container '<idle_c>' was just checked out and is now busy.`
  2. The container is NOT killed.
  3. Clean up the test container:
     ```python
     from tasks.pool.pool_manager import release_container
     release_container(tag, idle_c)
     ```
- **If it fails, what to check:**
  - Verify `admin_pools.py` checks `claimed = redis_client.srem(idle_key, target_name)`. If `claimed == 0`, it checks `sismember(busy_key)` and raises 409.

---

### Test Case 7 — Outside `docker rm -f`: container displays as "missing"

- **Goal:** Verify that if a container is destroyed outside the system (e.g. manually via `docker rm -f`), the UI displays it with state `missing` and memory `—`, and clicking delete cleans up the stale Redis entry.
- **Steps:**
  1. Pick an idle container name from the pool detail page (e.g. `patchwork_django_...`).
  2. In your terminal, force-remove it directly using Docker:
     ```powershell
     docker rm -f <container_name>
     ```
  3. Wait 5s for the next poll on `/pools/:name` (or click Refresh).
  4. Observe the container row in the table.
  5. Click **Delete** on the missing container and confirm.
- **Expected result:**
  1. The container row shows state badge: `missing` (in red/pink).
  2. Memory usage shows `—`.
  3. Clicking **Delete** succeeds (HTTP 204).
  4. The stale Redis entry is removed from `pool:{tag}:idle`.
  5. The table updates cleanly on the next poll.
- **If it fails, what to check:**
  - Check that `_inspect_container_info` catches `docker.errors.NotFound` and returns `ContainerOut(..., state="missing", memory_used_bytes=None)`.
  - Check that `delete_pool_container` detects `container_exists_in_docker == False` and removes the Redis member.

---

### Test Case 8 — Stopped or paused container: memory shows "—"

- **Goal:** Verify that a container that is not running (e.g. paused or stopped) displays `—` for memory and does not crash or break the detail view.
- **Steps:**
  1. Pick an idle container name from the table.
  2. Pause it via Docker:
     ```powershell
     docker pause <container_name>
     ```
  3. Refresh or wait 5s on `/pools/:name`.
  4. Observe the row.
  5. Unpause the container when done:
     ```powershell
     docker unpause <container_name>
     ```
- **Expected result:**
  1. The page loads without error.
  2. Memory column shows `—`.
  3. After unpausing, the next 5s poll resumes displaying normal memory stats (e.g. `24.5 MiB / 256 MiB (10%)`).
- **If it fails, what to check:**
  - Ensure `_inspect_container_info` checks `if status_str == "running"` before calling `c.stats(stream=False)`.

---

### Test Case 9 — Pool with max 0 (Empty State)

- **Goal:** Verify that an empty pool displays the empty state message.
- **Steps:**
  1. In **Manage Pools**, scale a test pool to `0` max containers (or create a new pool with max `0`).
  2. Wait for idle containers to drain.
  3. Click the pool name to open `/pools/<name>`.
- **Expected result:**
  1. Summary card shows Total: `0 / 0`, Free: `0`, Occupied: `0`, Total Pool Memory: `—`.
  2. Container table is replaced by the empty state box:
     *"No containers currently in this pool."*
     *"If max containers is greater than 0, the pool daemon will spawn warm idle containers shortly."*
- **If it fails, what to check:**
  - Verify `containers.length === 0` renders `.state-box`.

---

### Test Case 10 — Delete the pool while detail page is open (404 state)

- **Goal:** Verify that if the pool is deleted by another tab or API call while the detail page is open, the UI gracefully displays a 404 error with a link back to Manage Pools.
- **Steps:**
  1. Open `/pools/test-pool` in Browser Tab A.
  2. In Browser Tab B (or via curl), delete the pool:
     ```powershell
     curl -X DELETE "http://localhost:8000/api/admin/pools/test-pool" -H "Cookie: session=<admin-cookie>"
     ```
  3. Return to Tab A and wait 5s for the next poll (or click Refresh).
- **Expected result:**
  1. The detail page replaces the table with an error box:
     `Error loading pool: Pool 'test-pool' not found.`
  2. Clicking `&larr; Back to Manage Pools` safely navigates back to `/pools`.
- **If it fails, what to check:**
  - Verify `get_pool_containers` returns HTTP 404 when `get_pool_def(name)` is None.
  - Verify frontend renders the error state with back link.

---

### Test Case 11 — Non-overlapping polling & no duplicate timers

- **Goal:** Verify that polling uses non-overlapping sequential requests (`setTimeout` after response), and navigating away and back does not stack duplicate interval timers.
- **Steps:**
  1. Open Chrome DevTools (`F12`) on the **Network** tab.
  2. Filter by `Fetch/XHR`.
  3. Open `/pools/default-django`.
  4. Observe network requests: exactly one `containers` request every ~5 seconds.
  5. In the sidebar, click **Manage Images**, wait 3 seconds, then click **Manage Pools**, then click `default-django` again.
  6. Repeat navigation 3 times.
  7. Watch the Network tab for 15 seconds.
- **Expected result:**
  1. At no point are two `/containers` requests in flight simultaneously.
  2. There is strictly **one** request scheduled every 5 seconds; requests do not multiply or double up after navigating back and forth.
- **If it fails, what to check:**
  - Verify `useEffect` in `PoolDetail.jsx` clears `timerRef.current` in its cleanup function.
  - Verify polling is scheduled using `timerRef.current = setTimeout(...)` inside `scheduleNextPoll`, only *after* `fetchDetails()` completes.

---

### Test Case 12 — Direct URL navigation / browser reload on `/pools/:name`

- **Goal:** Verify that navigating directly to `/pools/:name` or pressing `F5` / Ctrl+R in the browser reloads the container detail view directly without routing to `/images`.
- **Steps:**
  1. Navigate to `/pools/default-django`.
  2. Press `Ctrl+R` (or `F5`) to hard-reload the browser.
- **Expected result:**
  1. The page reloads directly on `/pools/default-django`.
  2. Sidebar highlights **Manage Pools**.
  3. The container detail view for `default-django` renders with its overview card and container table.
- **If it fails, what to check:**
  - Verify `Shell.jsx` initial state parsing: `currentPath.match(/^\/pools\/(.+)$/)` correctly matches `detailPoolName`.

---

### Test Case 13 — Session expiry (401 Unauthorized)

- **Goal:** Verify that when the admin session expires, any container API request or deletion attempt redirects to the login screen.
- **Steps:**
  1. Open `/pools/default-django`.
  2. In DevTools -> Application -> Cookies, delete the `session` cookie.
  3. Wait up to 5s for the next polling request (or click **Delete** on any container).
- **Expected result:**
  1. The API call returns HTTP 401 Unauthorized.
  2. `apiFetch` in `api.js` dispatches the `auth:expired` event.
  3. The application clears state and renders the Login page immediately.
- **If it fails, what to check:**
  - Verify `apiFetch` checks `res.status === 401` and dispatches `window.dispatchEvent(new CustomEvent('auth:expired'))`.
