/**
 * Central fetch wrapper for all /api/admin requests.
 *
 * - Sends credentials (session cookie) on every call.
 * - On HTTP 401, dispatches a "auth:expired" CustomEvent so AuthContext
 *   can clear state and the app re-renders the login page.
 * - Returns { data, error, status } to avoid try/catch boilerplate at call sites.
 */

export async function apiFetch(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-cache, no-store, must-revalidate',
      'Pragma': 'no-cache',
      ...options.headers,
    },
    ...options,
  });

  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth:expired'));
    return { data: null, error: 'Session expired. Redirecting to login…', status: 401 };
  }

  let data = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    data = await res.json();
  }

  if (!res.ok) {
    const msg = data?.detail || `HTTP ${res.status}`;
    return { data: null, error: msg, status: res.status };
  }

  return { data, error: null, status: res.status };
}

// ── Images ──────────────────────────────────────────────────────────────────

export const listImages = () =>
  apiFetch('/api/admin/images');

export const createImage = (name, runtime) =>
  apiFetch('/api/admin/images', {
    method: 'POST',
    body: JSON.stringify({ name, runtime }),
  });

export const getImage = (name) =>
  apiFetch(`/api/admin/images/${encodeURIComponent(name)}`);

export const deleteImage = (name) =>
  apiFetch(`/api/admin/images/${encodeURIComponent(name)}`, { method: 'DELETE' });

export const addDependency = (imageName, pkgName, version) =>
  apiFetch(`/api/admin/images/${encodeURIComponent(imageName)}/dependencies`, {
    method: 'POST',
    body: JSON.stringify({ name: pkgName, version: version || undefined }),
  });

/**
 * Remove a dependency.
 * Scoped npm packages (e.g. @types/node) contain a slash, so they must be
 * double-encoded: the route is path-typed, so the first %2F is decoded by
 * FastAPI's path parser. We encode once (%40types%2Fnode) which after Vite
 * proxy passthrough reaches FastAPI as the literal string @types/node.
 */
export const removeDependency = (imageName, pkgName) => {
  const encodedPkg = encodeURIComponent(pkgName);
  return apiFetch(
    `/api/admin/images/${encodeURIComponent(imageName)}/dependencies/${encodedPkg}`,
    { method: 'DELETE' }
  );
};

export const previewBuild = (name) =>
  apiFetch(`/api/admin/images/${encodeURIComponent(name)}/preview-build`);

export const triggerBuild = (name) =>
  apiFetch(`/api/admin/images/${encodeURIComponent(name)}/build`, { method: 'POST' });

// ── Package search ───────────────────────────────────────────────────────────

export const searchNpm = (name) =>
  apiFetch(`/api/admin/packages/npm/search?name=${encodeURIComponent(name)}`);

export const searchPypi = (name) =>
  apiFetch(`/api/admin/packages/pypi/search?name=${encodeURIComponent(name)}`);

// ── Pools ────────────────────────────────────────────────────────────────────

export const listPools = () =>
  apiFetch('/api/admin/pools');

export const getPool = (name) =>
  apiFetch(`/api/admin/pools/${encodeURIComponent(name)}`);

export const createPool = (name, imageTag, maxContainers) =>
  apiFetch('/api/admin/pools', {
    method: 'POST',
    body: JSON.stringify({
      name,
      image_tag: imageTag,
      max_containers: Number(maxContainers),
    }),
  });

export const scalePool = (name, maxContainers) =>
  apiFetch(`/api/admin/pools/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify({
      max_containers: Number(maxContainers),
    }),
  });

export const deletePool = (name) =>
  apiFetch(`/api/admin/pools/${encodeURIComponent(name)}`, { method: 'DELETE' });

export const getAvailableImages = () =>
  apiFetch('/api/admin/pools/available-images');

