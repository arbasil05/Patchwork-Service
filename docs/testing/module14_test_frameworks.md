# Module 14 — Fixed Test Frameworks Manual Testing Guide

This guide covers step-by-step manual verification for the fixed test framework infrastructure in `patchwork_service`:
- Python images: `pytest==8.4.1` pinned in system site-packages.
- Node images: `jest 29.7.0` pinned as exact `devDependency` in `/workspace/package.json` with `"scripts": {"test": "jest --runInBand"}`.
- Rejection of reserved test frameworks (`pytest` / `jest`) from admin dependencies.
- Rebuild detection badge (`needs_rebuild`) on existing images whose tag predates the test framework update.

---

## Prerequisites & Environment Setup

All commands are run from the repository root (`d:\Patchwork-service\`).

1. **Redis**: Ensure Redis is running on `localhost:6379`.
2. **Docker**: Start Docker Desktop / Docker daemon (`docker info`).

### Terminal A — Backend API Server
```powershell
cd d:\Patchwork-service\backend
python -m uvicorn main:app --reload --port 8000
```

### Terminal B — RQ Background Worker
```powershell
cd d:\Patchwork-service\backend
python worker\run_worker.py
```

### Terminal C — Frontend Vite Dev Server
```powershell
cd d:\Patchwork-service\frontend
npm run dev
```
Open `http://localhost:5173` in your browser and log in with admin credentials.

---

## Test Cases

### Case 1: Preview-build shows fixed lines for each runtime

- **Goal:** Verify that the build preview displays the fixed test framework lines for both Python and Node runtime definitions.
- **Steps:**
  1. In the UI, navigate to **Manage Images**.
  2. Click **Create Image**, create a scratch Python definition named `preview-test-py` (runtime: Python).
  3. On the detail page for `preview-test-py`, click **Preview Dockerfile & Dependencies**.
  4. Repeat for a scratch Node definition named `preview-test-node` (runtime: Node).
- **Expected result:**
  - Python Preview:
    - Dockerfile contains `LABEL patchwork.test_framework="pytest==8.4.1"`.
    - `requirements.txt` contains `pytest==8.4.1` at the very top.
  - Node Preview:
    - Dockerfile contains `LABEL patchwork.test_framework="jest@29.7.0"`.
    - `package.json` contains:
      ```json
      "scripts": {
        "test": "jest --runInBand"
      },
      "devDependencies": {
        "jest": "29.7.0"
      }
      ```
- **If it fails, what to check:**
  - Check `GET /admin/images/{name}/preview-build` response in browser DevTools.
  - Verify `backend/services/image_build.py` generator functions `generate_requirements_txt` and `generate_package_json`.

---

### Case 2: Detail page shows locked "Included in every image" rows

- **Goal:** Verify that fixed test frameworks appear at the top of the Dependencies section as read-only rows without a Remove button.
- **Steps:**
  1. Open the detail page for any Python image (e.g. `preview-test-py` or `python-image`).
  2. Inspect the **Dependencies** card.
  3. Open the detail page for any Node image (e.g. `preview-test-node` or `node-image`).
  4. Inspect the **Dependencies** card.
- **Expected result:**
  - On Python image: `pytest` `v8.4.1` appears at the top with badge `Included in every image`. There is no "Remove" button next to it.
  - On Node image: `jest` `v29.7.0` appears at the top with badge `Included in every image`. There is no "Remove" button next to it.
- **If it fails, what to check:**
  - Inspect `GET /admin/images/{name}` response. Ensure `fixed_dependencies` is returned with `[{"name": "pytest", "version": "8.4.1"}]` or `[{"name": "jest", "version": "29.7.0"}]`.
  - Check `frontend/src/ImageDetail.jsx` rendering logic for `image.fixed_dependencies`.

---

### Case 3: Adding pytest or jest through the UI is rejected with a clear message

- **Goal:** Verify that attempting to add a reserved test framework as an admin dependency is blocked by the API and displays the clear error message inline.
- **Steps:**
  1. Navigate to the detail page for `preview-test-py`.
  2. In the "Add Dependency" search box, search for `pytest` (or `PyTest`).
  3. Select it and click **Add Dependency**.
  4. Navigate to the detail page for `preview-test-node`.
  5. In the "Add Dependency" search box, search for `jest` (or `JEST`).
  6. Select it and click **Add Dependency**.
- **Expected result:**
  - Python: An inline error message appears: `pytest 8.4.1 is pre-installed in every Python image and cannot be added`. The package is not added to the dependency list.
  - Node: An inline error message appears: `jest 29.7.0 is pre-installed in every Node image and cannot be added`. The package is not added to the dependency list.
- **If it fails, what to check:**
  - In DevTools Network tab, check `POST /admin/images/{name}/dependencies`. It must return status 400 with detail containing the message.
  - Verify `backend/config/test_frameworks.py` and `backend/routes/admin_images.py:add_dependency`.

---

### Case 4: New empty image builds and runs test framework under network-none

- **Goal:** Verify that clean builds of Python and Node images produce runnable test frameworks under `--network none`.
- **Steps:**
  1. Build `preview-test-py` (empty dependencies) by clicking **Build Image** (or run `python backend/worker/build_job.py preview-test-py`).
  2. Wait for status `success` and note the resulting image tag `patchwork/preview-test-py:<hash>`.
  3. In PowerShell, run:
     ```powershell
     docker run --rm --network none patchwork/preview-test-py:<hash> pytest --version
     docker run --rm --network none patchwork/preview-test-py:<hash> python -c "import sys; print(sys.prefix)"
     ```
  4. Build `preview-test-node` (empty dependencies) by clicking **Build Image** (or run `python backend/worker/build_job.py preview-test-node`).
  5. Wait for status `success` and note the resulting image tag `patchwork/preview-test-node:<hash>`.
  6. In PowerShell, run:
     ```powershell
     docker run --rm --network none patchwork/preview-test-node:<hash> npx --no-install jest --version
     docker run --rm --network none patchwork/preview-test-node:<hash> ls -la /workspace/node_modules/.bin/jest
     ```
- **Expected result:**
  - Python: `pytest 8.4.1` is output. Pytest binary is located in `/usr/local/bin/pytest` and runs without network access.
  - Node: `29.7.0` is output. Jest binary exists at `/workspace/node_modules/.bin/jest`.
- **If it fails, what to check:**
  - Check the build log on the image detail page.
  - For Node, ensure `npm install --omit=optional` ran with `NODE_ENV=development` so devDependencies were installed.

---

### Case 5: Container pool isolation test runs (passing and failing)

- **Goal:** Verify executing real passing and failing tests inside containers configured with the exact pool container flags.
- **Steps:**
  1. Run passing Python test:
     ```powershell
     docker run --rm --network none --read-only --tmpfs /tmp:exec --tmpfs /workspace:exec,size=64m,uid=1000,gid=1000 --memory 256m --pids-limit 256 patchwork/preview-test-py:<hash> sh -c "printf 'def test_ok():\n    assert 1 == 1\n' > /workspace/test_ok.py && cd /workspace && pytest -q"
     ```
  2. Run failing Python test:
     ```powershell
     docker run --rm --network none --read-only --tmpfs /tmp:exec --tmpfs /workspace:exec,size=64m,uid=1000,gid=1000 --memory 256m --pids-limit 256 patchwork/preview-test-py:<hash> sh -c "printf 'def test_fail():\n    assert 1 == 2\n' > /workspace/test_fail.py && cd /workspace && pytest -q"
     ```
  3. Run passing Node test:
     ```powershell
     docker run --rm --network none --tmpfs /tmp:exec --memory 256m --pids-limit 256 patchwork/preview-test-node:<hash> sh -c "printf 'test(\"ok\", () => { expect(1).toBe(1); });\n' > /workspace/ok.test.js && cd /workspace && npx jest --runInBand --ci"
     ```
  4. Run failing Node test:
     ```powershell
     docker run --rm --network none --tmpfs /tmp:exec --memory 256m --pids-limit 256 patchwork/preview-test-node:<hash> sh -c "printf 'test(\"fail\", () => { expect(1).toBe(2); });\n' > /workspace/fail.test.js && cd /workspace && npx jest --runInBand --ci"
     ```
- **Expected result:**
  - Passing tests: output `1 passed` (Python) and `Tests: 1 passed, 1 total` (Node). Exit code is `0`.
  - Failing tests: output assertion failure details. Exit code is `1` (non-zero).
- **If it fails, what to check:**
  - Check file permissions in `/workspace` (ensure `uid=1000,gid=1000` is used on Python tmpfs).
  - Verify that `.pytest_cache` can be written to `/workspace`.

---

### Case 6: Existing images show "Rebuild needed" and clear after rebuild

- **Goal:** Verify that existing images built prior to the fixed test framework changes show the "Rebuild needed" badge, and that rebuilding clears the badge and updates the image tag.
- **Steps:**
  1. Navigate to **Manage Images**.
  2. Inspect rows for `python-image`, `node-image`, and `python-ui-image`.
  3. Click on `python-image` to view its detail page.
  4. Click **Build Image** (or run `python backend/worker/build_job.py python-image`).
  5. Wait for the build job to finish.
- **Expected result:**
  - Before rebuild: `ImageList` and `ImageDetail` show a `Rebuild needed` badge next to `success`.
  - After rebuild: The image tag updates from `patchwork/python-image:13b2879db46f` to `patchwork/python-image:ea88796cec63`. The `Rebuild needed` badge disappears.
- **If it fails, what to check:**
  - Verify `compute_expected_image_tag(image_def)` in `backend/services/image_build.py`.
  - Check that `admin_images.py` recalculates `needs_rebuild` on `GET /admin/images` and `GET /admin/images/{name}`.

---

### Case 7: Behavior of a pool pointing to an old tag after rebuild

- **Goal:** Observe and document what happens when an existing pool configuration points to an old tag while the definition is rebuilt.
- **Steps:**
  1. Check an existing pool configured with `patchwork/python-image:13b2879db46f`.
  2. Rebuild definition `python-image` so that its definition tag becomes `patchwork/python-image:ea88796cec63`.
  3. Inspect the pool in **Manage Pools**.
- **Expected result:**
  - The pool configuration in Redis / pool storage still points to the previous tag (`patchwork/python-image:13b2879db46f`).
  - Warm containers in that pool continue running the old image.
  - The pool is NOT automatically migrated to the new image tag. (The admin must edit or recreate the pool to target the new tag).
- **If it fails, what to check:**
  - Check `backend/storage/pool_defs.py` and `backend/tasks/pool/pool_daemon.py`. Pool configurations are decoupled from image definition updates.

---

### Case 8: End-to-end execution through a pool

- **Goal:** Submit candidate test execution through `/v1/execute` targeting a newly built image with fixed test frameworks.
- **Steps:**
  1. Ensure a pool is active for the rebuilt image (e.g. `patchwork/preview-test-py:<hash>`).
  2. Send a POST request to `/v1/execute`:
     ```bash
     curl -X POST http://127.0.0.1:8000/v1/execute \
       -H "Content-Type: application/json" \
       -d '{
         "idempotency_key": "11111111-2222-3333-4444-555555555555",
         "image_tag": "patchwork/preview-test-py:<hash>",
         "files": [
           {"filename": "test_app.py", "content": "def test_answer(): assert 2 + 2 == 4\n"}
         ],
         "test_command": "pytest"
       }'
     ```
  3. Poll the returned poll URL (`/v1/status/11111111-2222-3333-4444-555555555555`).
- **Expected result:**
  - The worker acquires a warm container from the pool.
  - Submission files are injected into `/workspace`.
  - `pytest` executes successfully without downloading any files.
  - Status becomes `completed`, `exit_code: 0`, and `stdout` contains `1 passed in ...s`.
- **If it fails, what to check:**
  - In worker logs, check `backend/tasks/tasks.py:run_submission`.
  - Check if the pool daemon has provisioned idle warm containers for `image_tag`.

---

### Case 9: Incompatible dependency pin causes visible build failure

- **Goal:** Verify that if an admin specifies a dependency that conflicts with the fixed test framework, pip build fails with a visible error in the log instead of corrupting the environment.
- **Steps:**
  1. Create a scratch Python definition named `conflict-test-py`.
  2. Add a dependency that specifies an incompatible constraint with `pytest==8.4.1` (for example, a package pinning `pluggy<1.0` or `iniconfig<1.0`).
  3. Trigger build.
- **Expected result:**
  - The build fails during `pip install --no-cache-dir -r requirements.txt`.
  - Image definition status transitions to `failed`.
  - The build log in the image detail page clearly displays pip's conflict resolution error (e.g. `ResolutionImpossible` or `conflicting dependencies`).
- **If it fails, what to check:**
  - Check `build_log` on the image detail page.
  - Verify that both `pytest==8.4.1` and the admin's package were placed into the single `requirements.txt`.
