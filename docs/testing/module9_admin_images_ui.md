# Module 9 — Admin Images UI Manual Testing Guide

This guide provides end-to-end manual verification steps for the Admin Image Management UI in `patchwork_service`.

---

## Prerequisites & Environment Setup

All commands are run from the repository root (`d:\Patchwork-service\`).

1. **Redis**: Ensure Redis is running on `localhost:6379`.
2. **Docker**: Ensure Docker Desktop / Docker daemon is running (`docker info`).

### Terminal A — Backend API Server
```powershell
cd d:\Patchwork-service\backend
python -m uvicorn main:app --reload --port 8000
```
Verify `http://127.0.0.1:8000/docs` responds in your browser.

### Terminal B — RQ Background Worker
```powershell
cd d:\Patchwork-service\backend
python worker\run_worker.py
```
Worker will log that it is listening on queues `['submission_queue', 'image_builds']`.

### Terminal C — Frontend Vite Dev Server
```powershell
cd d:\Patchwork-service\frontend
npm run dev
```
Open `http://localhost:5173` in your browser.

---

## Test Cases

### Test Case 1 — Login, then land on Manage Images

- **Goal:** Authenticate as admin and verify automatic landing on the "Manage Images" section.
- **Steps:**
  1. Navigate to `http://localhost:5173`.
  2. If not already authenticated, the Admin Login form is displayed.
  3. Enter Username: `admin` and Password: `admin` (or your configured admin credentials).
  4. Click **Login**.
- **Expected result:**
  1. The authenticated shell renders with a left sidebar showing "Patchwork Admin" and a single navigation entry: "Manage Images" (highlighted as active).
  2. The URL normalizes to `http://localhost:5173/images`.
  3. The main area displays the "Manage Images" header with a **Create Image** button and the images table or empty state.
- **If it fails, what to check:**
  - In DevTools Network tab, check `POST /api/admin/login` response. If 401, check credentials in `backend/.env`.
  - Check `GET /api/admin/me` response. Ensure cookies are accepted by the browser (`credentials: 'same-origin'`).

---

### Test Case 2 — Empty state, then create a Python image and a Node image

- **Goal:** Verify the empty state when no definitions exist, and successfully create Python and Node image definitions.
- **Steps:**
  1. Ensure `saved_docker_images/` in `backend/` has no JSON files (backup any existing definitions to a temp folder if needed).
  2. Refresh the page at `http://localhost:5173/images`.
  3. Verify the empty state message: *"No image definitions found. Click 'Create Image' above to add your first image definition."*
  4. Click the **Create Image** button at the top right.
  5. In the modal:
     - Name: `py-worker`
     - Runtime: Select **Python**
     - Click **Create**.
  6. Verify you are automatically redirected to the detail page `http://localhost:5173/images/py-worker`.
  7. Click **← Back to Manage Images**.
  8. Click **Create Image** again:
     - Name: `node-service`
     - Runtime: Select **Node**
     - Click **Create**.
- **Expected result:**
  1. Both `py-worker` and `node-service` appear in the table with Status `draft`, 0 dependencies, Image Tag `—`, and their respective runtimes (`python` and `node`).
  2. The backend filesystem contains `saved_docker_images/py-worker.json` and `saved_docker_images/node-service.json`.
- **If it fails, what to check:**
  - Check Network tab for `POST /api/admin/images`. Status code should be 201 Created.
  - Verify file system permissions on `saved_docker_images/`.

---

### Test Case 3 — Validation errors: invalid name, duplicate name

- **Goal:** Verify that client-side and server-side validation errors are displayed inline without crashing.
- **Steps:**
  1. Click **Create Image**.
  2. Enter an invalid name with spaces and uppercase letters: `My Image/Test`.
  3. Click **Create**.
  4. Observe client-side validation error: *"Name must contain only lowercase letters, numbers, hyphens, and underscores (no spaces, slashes, or uppercase)."* The form is not submitted.
  5. Change the name to an existing definition: `py-worker`.
  6. Click **Create**.
- **Expected result:**
  1. An inline error banner appears inside the modal: *"Image definition 'py-worker' already exists"*.
  2. The modal remains open, allowing the admin to correct the name.
  3. Click **Cancel** to dismiss the modal cleanly.
- **If it fails, what to check:**
  - Check Network tab for HTTP 409 Conflict response on duplicate name.
  - Ensure the client regex `/^[a-z0-9_-]+$/` correctly flags spaces, slashes, and uppercase letters.

---

### Test Case 4 — Add dependencies via search (NPM & PyPI, scoped packages, pinned versions, nonexistent package/version)

- **Goal:** Search and add dependencies for both runtimes, validating registry search, version picking, custom version entry, and error handling.
- **Steps:**
  1. Click `py-worker` from the images list to open its detail page (`/images/py-worker`).
  2. **PyPI valid package with latest version:**
     - In the search box, type `requests`.
     - Wait ~300ms. A dropdown result appears: `requests (latest: <version>)`.
     - Click the result. The version input defaults to the latest version.
     - Click **Add Dependency**.
     - Verify `requests` appears in the Dependencies list with the selected version.
  3. **PyPI pinned older version:**
     - In search box, type `requests`. Click the result.
     - In the version dropdown or text box, select or type `2.31.0`.
     - Click **Add Dependency**.
     - Verify `requests` updates to `v2.31.0` (upserted).
  4. **PyPI nonexistent package:**
     - In the search box, type `nonexistent-pkg-xyz-12345`.
     - The dropdown shows: *Package "nonexistent-pkg-xyz-12345" not found in python registry.*
  5. **PyPI invalid version:**
     - Search `requests`, select it.
     - In the version text input, enter an invalid/nonexistent version: `999.999.999`.
     - Click **Add Dependency**.
     - An inline error banner appears: *"Version '999.999.999' not found for package 'requests' in the last 20 releases."*
  6. **Node.js scoped package (@types/node):**
     - Click **← Back to Manage Images** and click `node-service`.
     - In the search box, type `@types/node`.
     - Wait ~300ms. The result `@types/node` appears in the dropdown.
     - Click it, leave the latest version selected, and click **Add Dependency**.
     - Verify `@types/node` appears in the Dependencies list with version (e.g. `v22.x.x`).
  7. **Node.js regular package:**
     - Search `express`. Select it. Click **Add Dependency**.
     - Verify `express` appears in the list.
- **Expected result:**
  1. Dependencies are successfully added and displayed with name and version.
  2. Nonexistent packages show clear "not found" notifications in the search dropdown.
  3. Invalid versions display the exact backend error message inline.
- **If it fails, what to check:**
  - Check Network tab for `GET /api/admin/packages/pypi/search?name=...` and `GET /api/admin/packages/npm/search?name=...`.
  - Check `POST /api/admin/images/{name}/dependencies` response body for `detail`.

---

### Test Case 5 — Remove dependencies, including scoped package

- **Goal:** Verify removing dependencies works for standard packages and scoped packages with slashes (`@types/node`).
- **Steps:**
  1. On the `node-service` detail page, locate `@types/node` in the Dependencies list.
  2. Click **Remove** next to `@types/node`.
  3. Locate `express` in the list and click **Remove**.
- **Expected result:**
  1. The Remove button enters a loading state ("Removing…") and is disabled.
  2. Both dependencies are removed from the list.
  3. The message *"No dependencies added yet."* appears when the list is empty.
- **If it fails, what to check:**
  - Check the Network tab for `DELETE /api/admin/images/node-service/dependencies/%40types%2Fnode`.
  - Status should be 200 OK with the updated image definition returned.

---

### Test Case 6 — Preview build output for both runtimes

- **Goal:** Preview generated Dockerfile and dependency files before triggering a build.
- **Steps:**
  1. On `py-worker` detail page (add `requests` if not already present):
     - Click **Preview Build**.
     - Verify the build context block expands:
       - **Dockerfile**: Contains `FROM python:3.12.11-slim-bookworm`, `COPY requirements.txt .`, etc.
       - **requirements.txt**: Contains `requests==2.31.0` (or configured version).
     - Click **Hide Preview** to collapse it.
  2. On `node-service` detail page (add `express` as a dependency):
     - Click **Preview Build**.
     - Verify the build context block expands:
       - **Dockerfile**: Contains `FROM node:22-bookworm-slim`, `COPY package.json ./`, `npm install`, etc.
       - **package.json**: Contains valid JSON with `"name": "node-service"`, `"dependencies": { "express": "..." }`.
- **Expected result:**
  1. Both preview files are displayed in read-only monospace blocks without errors.
- **If it fails, what to check:**
  - Network tab for `GET /api/admin/images/{name}/preview-build`. Check response status (200 OK) and JSON keys (`dockerfile`, `dependency_file_name`, `dependency_file_content`).

---

### Test Case 7 — Build a Python image: status progression & automatic polling

- **Goal:** Trigger an image build, observe `queued` -> `building` -> `success` status progression with automatic polling, verify log output, and confirm Docker image exists.
- **Steps:**
  1. On `py-worker` detail page, ensure at least one valid dependency is present (e.g. `requests`).
  2. Click **Build Image**.
  3. Watch the UI without refreshing the page.
- **Expected result:**
  1. Status immediately changes to `queued` with an active pulsing indicator.
  2. The RQ worker picks up the job and status updates to `building` (via 2.5s polling).
  3. An informational banner indicates: *"Build is currently building. Polling for updates…"*.
  4. The **Build Image** button is disabled during the build.
  5. Once complete, status transitions to `success` with green badge.
  6. The `image_tag` displays `patchwork/py-worker:<12-char-hash>`.
  7. The build log displays cleanly in the monospace block. ANSI codes and carriage returns are stripped.
  8. Click **Copy Log**: the button temporarily changes to "Copied!".
  9. Run `docker images` in a terminal: verify `patchwork/py-worker:<hash>` exists.
- **If it fails, what to check:**
  - Ensure Terminal B (RQ worker) is running and listening on queue `image_builds`.
  - Check worker console logs for Docker SDK errors.
  - In DevTools Network tab, verify polling requests to `GET /api/admin/images/py-worker` happen every ~2.5s and stop once status is `success`.

---

### Test Case 8 — Build failure: UI handling and error log display

- **Goal:** Verify that build failures are handled gracefully, status becomes `failed`, and errors are prominently highlighted.
- **Steps:**
  1. Create an image definition named `fail-test` (Python).
  2. Add a dependency. To simulate an unbuildable scenario without modifying backend code:
     - Temporarily edit `backend/saved_docker_images/fail-test.json`.
     - Change the dependency version to something invalid like `"version": "invalid-version-xyz"`.
     - Save the JSON file.
  3. On `/images/fail-test` in the browser, click **Build Image**.
  4. Wait for the RQ worker to run the build.
- **Expected result:**
  1. The pip build step fails inside Docker.
  2. Status transitions to `failed` (red badge).
  3. A prominent red error alert appears: *"Build Failed! The Docker build failed. Please inspect the build log below."*
  4. The build log displays pip's error output.
  5. The **Build Image** button is re-enabled so the admin can fix the issue and retry.
- **If it fails, what to check:**
  - Inspect `saved_docker_images/fail-test.json` to verify `status: "failed"` and `build_log` contains the error.
  - Check worker console for `BuildError`.

---

### Test Case 9 — Duplicate build click and 409 Conflict handling

- **Goal:** Verify that clicking Build Image while a build is already queued or in progress does not submit duplicate jobs and displays a clear 409 message if attempted.
- **Steps:**
  1. The **Build Image** button is disabled on the client side whenever status is `queued` or `building`.
  2. To test backend 409 handling: trigger a build on an image that takes some time.
  3. Open DevTools Console and execute:
     ```js
     fetch('/api/admin/images/py-worker/build', { method: 'POST', credentials: 'same-origin' })
       .then(r => r.json()).then(console.log);
     ```
- **Expected result:**
  1. The API responds with HTTP 409 Conflict: `{"detail": "Image definition 'py-worker' is already queued"}` (or `...is already building`).
  2. If triggered via UI, an inline error banner displays: *"Build request error: Image definition 'py-worker' is already queued"*.
- **If it fails, what to check:**
  - Verify `backend/routes/admin_images.py` line 205 checks `if image_def.get("status") in ("building", "queued")`.

---

### Test Case 10 — Navigate away during build and return (no duplicate polling)

- **Goal:** Verify that polling stops when unmounting the detail page and resumes cleanly upon returning, with no duplicate intervals.
- **Steps:**
  1. Trigger a build on an image.
  2. While the status is `building`, open DevTools Network tab and filter by `Fetch/XHR`.
  3. Note that `GET /api/admin/images/{name}` is called every ~2.5 seconds.
  4. Click **← Back to Manage Images** in the UI.
  5. Look at the Network tab: polling immediately stops (no more requests are fired).
  6. Wait 5 seconds, then click the image row in the table to re-enter the detail page.
  7. Check Network tab: polling resumes with exactly one request every 2.5 seconds (never paired or doubled).
- **Expected result:**
  1. Cleanup function of the React polling effect runs on unmount.
  2. No memory leak or duplicate polling intervals occur.
- **If it fails, what to check:**
  - Inspect `useEffect` cleanup in `ImageDetail.jsx`. Verify `clearInterval` is invoked.

---

### Test Case 11 — Delete image definition and verify Docker image remains

- **Goal:** Confirm the delete confirmation warning and verify that deleting a definition removes the JSON file but keeps the Docker image.
- **Steps:**
  1. On the Manage Images page, find `py-worker` (which was successfully built in Test Case 7).
  2. Click the **Delete** button in its row.
  3. The confirmation modal appears with the text:
     *"Are you sure you want to delete the image definition 'py-worker'? Warning: This removes only the definition file. Any Docker image already built is NOT removed from Docker."*
  4. Click **Delete Definition**.
  5. The row disappears from the table.
  6. In terminal:
     - Check `saved_docker_images/`: `py-worker.json` is deleted.
     - Run `docker images patchwork/py-worker`: the image is still present in Docker.
- **Expected result:**
  1. Deletion removes the definition from Patchwork storage.
  2. Existing Docker images are preserved.
- **If it fails, what to check:**
  - Check Network tab for `DELETE /api/admin/images/py-worker` (HTTP 204 No Content).
  - Check `backend/storage/image_defs.py` `delete_image_def`.

---

### Test Case 12 — Session expiry and 401 redirect

- **Goal:** Verify that on 401 Unauthorized, client auth state is cleared and the admin is redirected to the login page.
- **Steps:**
  1. On the Manage Images page (or any image detail page).
  2. In another tab or DevTools console, clear the session cookie or call:
     ```js
     fetch('/api/admin/logout', { method: 'POST', credentials: 'same-origin' });
     ```
  3. Return to the admin UI tab and click any action (e.g. click "Create Image" or "Preview Build" or "Delete").
- **Expected result:**
  1. The API request returns HTTP 401 Unauthorized.
  2. `apiFetch` dispatches the `auth:expired` event.
  3. `AuthContext` clears authentication state (`isAuthenticated = false`).
  4. The UI immediately renders the Admin Login screen.
- **If it fails, what to check:**
  - Verify `api.js` line 20-23 dispatches `auth:expired`.
  - Verify `AuthContext.jsx` has an event listener for `auth:expired` that calls `setIsAuthenticated(false)`.

---

### Test Case 13 — Refresh page on detail route (/images/:name) without 404

- **Goal:** Verify that refreshing the browser on a deep detail route (`/images/node-service`) does not return a 404 from Vite and renders the detail view directly.
- **Steps:**
  1. Navigate to `http://localhost:5173/images/node-service`.
  2. Press browser Refresh (F5 / Cmd+R).
- **Expected result:**
  1. Vite serves `index.html` (HTTP 200).
  2. The application checks the session cookie with `GET /api/admin/me` (HTTP 200).
  3. The detail page for `node-service` loads directly with all headers, dependencies, and build sections.
  4. No 404 or white screen.
- **If it fails, what to check:**
  - Verify asset URLs in `index.html` use absolute paths (`/src/main.jsx`).
  - Verify `Shell.jsx` parses `window.location.pathname` on initial render.
