import { useState, useEffect } from 'react';
import { listImages, createImage, deleteImage } from './api';

export default function ImageList({ onNavigate }) {
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Create form state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createRuntime, setCreateRuntime] = useState('python');
  const [createError, setCreateError] = useState(null);
  const [isCreating, setIsCreating] = useState(false);

  // Delete modal state
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const fetchImages = async () => {
    setLoading(true);
    setError(null);
    const { data, error: err } = await listImages();
    if (err) {
      setError(err);
    } else {
      setImages(data || []);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchImages();
  }, []);

  // Auto-refresh list if any image is currently queued or building
  const hasActiveBuild = images.some((img) => img.status === 'queued' || img.status === 'building');

  useEffect(() => {
    let isSubscribed = true;

    const refresh = async () => {
      const { data, error: err } = await listImages();
      if (!isSubscribed) return;
      if (!err && data) {
        setImages(data);
      }
    };

    let intervalId = null;
    if (hasActiveBuild) {
      intervalId = setInterval(refresh, 2500);
    }

    const handleFocus = () => {
      if (document.visibilityState === 'visible') {
        refresh();
      }
    };

    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleFocus);

    return () => {
      isSubscribed = false;
      if (intervalId) clearInterval(intervalId);
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleFocus);
    };
  }, [hasActiveBuild]);

  const handleOpenCreate = () => {
    setCreateName('');
    setCreateRuntime('python');
    setCreateError(null);
    setShowCreateModal(true);
  };

  const handleCreateSubmit = async (e) => {
    e.preventDefault();
    setCreateError(null);

    const trimmedName = createName.trim();
    if (!trimmedName) {
      setCreateError('Image name is required.');
      return;
    }

    // Client-side validation matching backend rules:
    // lowercase, alphanumeric, hyphens, and underscores; no spaces or slashes
    const validPattern = /^[a-z0-9_-]+$/;
    if (!validPattern.test(trimmedName)) {
      setCreateError(
        'Name must contain only lowercase letters, numbers, hyphens, and underscores (no spaces, slashes, or uppercase).'
      );
      return;
    }

    setIsCreating(true);
    const { data, error: err } = await createImage(trimmedName, createRuntime);
    setIsCreating(false);

    if (err) {
      setCreateError(err);
    } else {
      setShowCreateModal(false);
      // Refresh list or append and navigate
      setImages((prev) => [...prev.filter((i) => i.name !== data.name), data]);
      if (onNavigate) {
        onNavigate(`/images/${encodeURIComponent(data.name)}`);
      }
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    setDeleteError(null);

    const { error: err } = await deleteImage(deleteTarget.name);
    setIsDeleting(false);

    if (err) {
      setDeleteError(err);
    } else {
      setImages((prev) => prev.filter((i) => i.name !== deleteTarget.name));
      setDeleteTarget(null);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Manage Images</h1>
        <button
          className="btn btn-primary"
          onClick={handleOpenCreate}
        >
          Create Image
        </button>
      </div>

      {loading && (
        <div className="state-box">
          <p>Loading image definitions…</p>
        </div>
      )}

      {!loading && error && (
        <div className="state-box">
          <p style={{ color: '#c0392b' }}>Error loading images: {error}</p>
          <button className="btn btn-secondary" onClick={fetchImages} style={{ marginTop: '1rem' }}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && images.length === 0 && (
        <div className="state-box">
          <p>No image definitions found.</p>
          <p style={{ marginTop: '0.5rem', fontSize: '14px' }}>
            Click &ldquo;Create Image&rdquo; above to add your first image definition.
          </p>
        </div>
      )}

      {!loading && !error && images.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Runtime</th>
              <th>Status</th>
              <th>Dependencies</th>
              <th>Image Tag</th>
              <th>Updated At</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {images.map((img) => (
              <tr key={img.name}>
                <td>
                  <a
                    className="table-link"
                    href={`/images/${encodeURIComponent(img.name)}`}
                    onClick={(e) => {
                      e.preventDefault();
                      onNavigate(`/images/${encodeURIComponent(img.name)}`);
                    }}
                  >
                    {img.name}
                  </a>
                </td>
                <td>{img.runtime}</td>
                <td>
                  <span className={`status-badge status-${img.status}`}>
                    {img.status}
                  </span>
                </td>
                <td>{img.dependencies ? img.dependencies.length : 0}</td>
                <td style={{ fontFamily: 'monospace', fontSize: '13px' }}>
                  {img.image_tag || '—'}
                </td>
                <td style={{ fontSize: '13px', color: '#666' }}>
                  {img.updated_at ? new Date(img.updated_at).toLocaleString() : '—'}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button
                    className="btn btn-danger"
                    onClick={() => {
                      setDeleteError(null);
                      setDeleteTarget(img);
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Create Image Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => !isCreating && setShowCreateModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Create Image Definition</h2>
            {createError && <div className="msg-error">{createError}</div>}
            <form onSubmit={handleCreateSubmit}>
              <div className="form-group">
                <label className="form-label" htmlFor="create-image-name">
                  Image Name
                </label>
                <input
                  id="create-image-name"
                  className="form-input"
                  type="text"
                  placeholder="e.g. data-processor"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  disabled={isCreating}
                  autoFocus
                  style={{ color: '#111', backgroundColor: '#fff' }}
                  required
                />
                <span style={{ fontSize: '12px', color: '#666', display: 'block', marginTop: '4px' }}>
                  Lowercase letters, numbers, hyphens, and underscores only.
                </span>
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="create-image-runtime">
                  Runtime
                </label>
                <select
                  id="create-image-runtime"
                  className="form-select"
                  value={createRuntime}
                  onChange={(e) => setCreateRuntime(e.target.value)}
                  disabled={isCreating}
                  style={{ color: '#111', backgroundColor: '#fff' }}
                >
                  <option value="python">Python</option>
                  <option value="node">Node</option>
                </select>
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
                  disabled={isCreating}
                >
                  {isCreating ? 'Creating…' : 'Create'}
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
            <h2 className="modal-title">Delete Image Definition</h2>
            {deleteError && <div className="msg-error">{deleteError}</div>}
            <div className="modal-body">
              Are you sure you want to delete the image definition &ldquo;<strong>{deleteTarget.name}</strong>&rdquo;?
              <br /><br />
              <strong>Warning:</strong> This removes only the definition file. Any Docker image already built is <strong>NOT</strong> removed from Docker.
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
                onClick={confirmDelete}
                disabled={isDeleting}
              >
                {isDeleting ? 'Deleting…' : 'Delete Definition'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
