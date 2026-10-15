import { useState, useEffect } from 'react';
import {
  listPools,
  createPool,
  scalePool,
  deletePool,
  getAvailableImages,
} from './api';

export default function PoolList() {
  const [pools, setPools] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Available images for pool creation
  const [availableImages, setAvailableImages] = useState([]);

  // Create pool modal
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createImageTag, setCreateImageTag] = useState('');
  const [createMaxContainers, setCreateMaxContainers] = useState(3);
  const [createError, setCreateError] = useState(null);
  const [isCreating, setIsCreating] = useState(false);

  // Scale modal
  const [scaleTarget, setScaleTarget] = useState(null);
  const [scaleMaxContainers, setScaleMaxContainers] = useState(3);
  const [scaleError, setScaleError] = useState(null);
  const [isScaling, setIsScaling] = useState(false);

  // Delete modal
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const fetchPools = async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    const { data, error: err } = await listPools();
    if (err) {
      setError(err);
    } else {
      setPools(data || []);
    }
    if (!silent) setLoading(false);
  };

  useEffect(() => {
    fetchPools();
  }, []);

  // Real-time polling every 2.5s and instant refresh on window focus
  useEffect(() => {
    let isSubscribed = true;

    const poll = async () => {
      const { data, error: err } = await listPools();
      if (!isSubscribed) return;
      if (!err && data) {
        setPools(data);
      }
    };

    const intervalId = setInterval(poll, 2500);

    const handleFocus = () => {
      if (document.visibilityState === 'visible') {
        poll();
      }
    };

    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleFocus);

    return () => {
      isSubscribed = false;
      clearInterval(intervalId);
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleFocus);
    };
  }, []);

  const handleOpenCreate = async () => {
    setCreateName('');
    setCreateImageTag('');
    setCreateMaxContainers(3);
    setCreateError(null);

    // Fetch fresh available images
    const { data } = await getAvailableImages();
    const imgs = data || [];
    setAvailableImages(imgs);

    // Default select first available unpooled image, or first image
    const unpooled = imgs.find((img) => !img.already_pooled);
    if (unpooled) {
      setCreateImageTag(unpooled.image_tag);
    } else if (imgs.length > 0) {
      setCreateImageTag(imgs[0].image_tag);
    }

    setShowCreateModal(true);
  };

  const handleCreateSubmit = async (e) => {
    e.preventDefault();
    setCreateError(null);

    const trimmedName = createName.trim();
    if (!trimmedName) {
      setCreateError('Pool name is required.');
      return;
    }

    const validPattern = /^[a-zA-Z0-9_-]+$/;
    if (!validPattern.test(trimmedName)) {
      setCreateError('Pool name must contain only alphanumeric characters, hyphens, and underscores.');
      return;
    }

    if (!createImageTag) {
      setCreateError('Please select a Docker image for the pool.');
      return;
    }

    const maxVal = Number(createMaxContainers);
    if (isNaN(maxVal) || maxVal < 0 || maxVal > 10) {
      setCreateError('Max containers must be between 0 and 10.');
      return;
    }

    setIsCreating(true);
    const { data, error: err } = await createPool(trimmedName, createImageTag, maxVal);
    setIsCreating(false);

    if (err) {
      setCreateError(err);
    } else {
      setShowCreateModal(false);
      setPools((prev) => [...prev.filter((p) => p.name !== data.name), data]);
    }
  };

  const handleOpenScale = (pool) => {
    setScaleTarget(pool);
    setScaleMaxContainers(pool.max_containers);
    setScaleError(null);
  };

  const handleScaleSubmit = async (e) => {
    e.preventDefault();
    if (!scaleTarget) return;

    const maxVal = Number(scaleMaxContainers);
    if (isNaN(maxVal) || maxVal < 0 || maxVal > 10) {
      setScaleError('Max containers must be between 0 and 10.');
      return;
    }

    setIsScaling(true);
    setScaleError(null);
    const { data, error: err } = await scalePool(scaleTarget.name, maxVal);
    setIsScaling(false);

    if (err) {
      setScaleError(err);
    } else {
      setScaleTarget(null);
      setPools((prev) => prev.map((p) => (p.name === data.name ? data : p)));
    }
  };

  const handleOpenDelete = (pool) => {
    setDeleteTarget(pool);
    setDeleteError(null);
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    setDeleteError(null);
    const { error: err } = await deletePool(deleteTarget.name);
    setIsDeleting(false);

    if (err) {
      setDeleteError(err);
    } else {
      setPools((prev) => prev.filter((p) => p.name !== deleteTarget.name));
      setDeleteTarget(null);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Manage Container Pools</h1>
        <button className="btn btn-primary" onClick={handleOpenCreate}>
          Create Pool
        </button>
      </div>

      {loading && (
        <div className="state-box">
          <p>Loading container pools…</p>
        </div>
      )}

      {!loading && error && (
        <div className="state-box">
          <p style={{ color: '#c0392b' }}>Error loading pools: {error}</p>
          <button className="btn btn-secondary" onClick={() => fetchPools()} style={{ marginTop: '1rem' }}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && pools.length === 0 && (
        <div className="state-box">
          <p>No container pools configured.</p>
          <p style={{ marginTop: '0.5rem', fontSize: '14px' }}>
            Click &ldquo;Create Pool&rdquo; above to provision a warm container pool.
          </p>
        </div>
      )}

      {!loading && !error && pools.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Pool Name</th>
              <th>Image Tag</th>
              <th>Max (Ceiling)</th>
              <th>Total</th>
              <th>Free</th>
              <th>Occupied</th>
              <th>State</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {pools.map((p) => {
              const isDraining = p.total > p.max_containers;
              return (
                <tr key={p.name}>
                  <td>
                    <strong>{p.name}</strong>
                    {p.is_default && (
                      <span
                        className="status-badge"
                        style={{ background: '#e2e3e5', color: '#383d41', marginLeft: '6px' }}
                      >
                        default
                      </span>
                    )}
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '13px' }}>
                    {p.image_tag}
                  </td>
                  <td>
                    <strong>{p.max_containers}</strong>
                  </td>
                  <td>{p.total}</td>
                  <td>
                    <span style={{ color: p.free > 0 ? '#155724' : '#888', fontWeight: 600 }}>
                      {p.free}
                    </span>
                  </td>
                  <td>
                    <span style={{ color: p.occupied > 0 ? '#856404' : '#888', fontWeight: 600 }}>
                      {p.occupied}
                    </span>
                  </td>
                  <td>
                    {isDraining ? (
                      <span className="status-badge status-building">
                        draining to {p.max_containers}…
                      </span>
                    ) : p.max_containers === 0 ? (
                      <span className="status-badge status-draft">drained (0)</span>
                    ) : p.free > 0 ? (
                      <span className="status-badge status-success">ready</span>
                    ) : (
                      <span className="status-badge status-queued">warming up…</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', gap: '0.4rem', justifyContent: 'flex-end' }}>
                      <button
                        className="btn btn-secondary"
                        style={{ padding: '0.25rem 0.6rem', fontSize: '13px' }}
                        onClick={() => handleOpenScale(p)}
                      >
                        Scale
                      </button>
                      {!p.is_default && (
                        <button
                          className="btn btn-danger"
                          style={{ padding: '0.25rem 0.6rem', fontSize: '13px' }}
                          onClick={() => handleOpenDelete(p)}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {/* Create Pool Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => !isCreating && setShowCreateModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Create Container Pool</h2>
            {createError && <div className="msg-error">{createError}</div>}
            <form onSubmit={handleCreateSubmit}>
              <div className="form-group">
                <label className="form-label" htmlFor="create-pool-name">
                  Pool Name
                </label>
                <input
                  id="create-pool-name"
                  className="form-input"
                  type="text"
                  placeholder="e.g. py-worker-pool"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  disabled={isCreating}
                  autoFocus
                  required
                />
                <span style={{ fontSize: '12px', color: '#666', display: 'block', marginTop: '4px' }}>
                  Alphanumeric characters, hyphens, and underscores only.
                </span>
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="create-pool-image">
                  Docker Image
                </label>
                <select
                  id="create-pool-image"
                  className="form-select"
                  value={createImageTag}
                  onChange={(e) => setCreateImageTag(e.target.value)}
                  disabled={isCreating}
                  required
                >
                  {availableImages.map((img) => (
                    <option
                      key={img.image_tag}
                      value={img.image_tag}
                      disabled={img.already_pooled}
                    >
                      {img.display_name} {img.already_pooled ? '(already has a pool)' : ''}
                    </option>
                  ))}
                </select>
                {availableImages.length === 0 && (
                  <span style={{ fontSize: '12px', color: '#c0392b', display: 'block', marginTop: '4px' }}>
                    No built images found. Build an image in &ldquo;Manage Images&rdquo; first.
                  </span>
                )}
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="create-pool-max">
                  Max Containers (0 – 10)
                </label>
                <input
                  id="create-pool-max"
                  className="form-input"
                  type="number"
                  min="0"
                  max="10"
                  value={createMaxContainers}
                  onChange={(e) => setCreateMaxContainers(e.target.value)}
                  disabled={isCreating}
                  required
                />
                <span style={{ fontSize: '12px', color: '#666', display: 'block', marginTop: '4px' }}>
                  Ceiling. The pool keeps a few containers warm and grows to this limit under load.
                </span>
              </div>

              <div className="modal-actions" style={{ marginTop: '1.5rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowCreateModal(false)}
                  disabled={isCreating}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isCreating || availableImages.length === 0}
                >
                  {isCreating ? 'Creating…' : 'Create Pool'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Scale Pool Modal */}
      {scaleTarget && (
        <div className="modal-overlay" onClick={() => !isScaling && setScaleTarget(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Scale Pool: {scaleTarget.name}</h2>
            {scaleError && <div className="msg-error">{scaleError}</div>}
            <form onSubmit={handleScaleSubmit}>
              <div style={{ marginBottom: '1rem', fontSize: '14px', color: '#555' }}>
                Current allocation: <strong>{scaleTarget.total}</strong> total (
                {scaleTarget.free} free, {scaleTarget.occupied} occupied).
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="scale-pool-max">
                  Max Containers (0 – 10)
                </label>
                <input
                  id="scale-pool-max"
                  className="form-input"
                  type="number"
                  min="0"
                  max="10"
                  value={scaleMaxContainers}
                  onChange={(e) => setScaleMaxContainers(e.target.value)}
                  disabled={isScaling}
                  autoFocus
                  required
                />
                <span style={{ fontSize: '12px', color: '#666', display: 'block', marginTop: '4px' }}>
                  Ceiling. The pool keeps a few containers warm and grows to this limit under load.
                </span>
              </div>

              <div className="modal-actions" style={{ marginTop: '1.5rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setScaleTarget(null)}
                  disabled={isScaling}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isScaling}
                >
                  {isScaling ? 'Scaling…' : 'Save Scaling'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <div className="modal-overlay" onClick={() => !isDeleting && setDeleteTarget(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Delete Pool</h2>
            {deleteError && <div className="msg-error">{deleteError}</div>}
            <div className="modal-body">
              Are you sure you want to delete pool &ldquo;<strong>{deleteTarget.name}</strong>&rdquo; (
              <code>{deleteTarget.image_tag}</code>)?
              <br /><br />
              All warm idle containers in this pool will be stopped and removed. The Docker image itself will not be deleted.
            </div>
            <div className="modal-actions">
              <button
                className="btn btn-secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={isDeleting}
              >
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={handleConfirmDelete}
                disabled={isDeleting}
              >
                {isDeleting ? 'Deleting…' : 'Delete Pool'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
