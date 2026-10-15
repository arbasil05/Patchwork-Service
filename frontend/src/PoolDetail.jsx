import { useState, useEffect, useRef, useCallback } from 'react';
import { getPoolContainers, deleteContainer, suspendPool, activatePool } from './api';

function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return '—';
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KiB`;
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}

function formatDate(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleString();
  } catch {
    return '—';
  }
}

export default function PoolDetail({ name, onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Deletion modal state
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [canForceDelete, setCanForceDelete] = useState(false);

  // Suspend modal & activate state
  const [showSuspendModal, setShowSuspendModal] = useState(false);
  const [isSuspending, setIsSuspending] = useState(false);
  const [suspendError, setSuspendError] = useState(null);
  const [isActivating, setIsActivating] = useState(false);

  const isSubscribedRef = useRef(true);
  const timerRef = useRef(null);

  const fetchDetails = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    const res = await getPoolContainers(name);
    if (!isSubscribedRef.current) return;

    if (res.error) {
      if (!silent) setError(res.error);
    } else {
      setError(null);
      setData(res.data);
    }
    if (!silent) setLoading(false);
  }, [name]);

  // Non-overlapping 5-second polling loop
  useEffect(() => {
    isSubscribedRef.current = true;

    const scheduleNextPoll = () => {
      if (!isSubscribedRef.current) return;
      timerRef.current = setTimeout(async () => {
        if (!isSubscribedRef.current) return;
        await fetchDetails(true);
        scheduleNextPoll();
      }, 5000);
    };

    fetchDetails(false).then(() => {
      scheduleNextPoll();
    });

    return () => {
      isSubscribedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fetchDetails]);

  const handleOpenDelete = (c) => {
    setDeleteTarget(c);
    setDeleteError(null);
    setCanForceDelete(c.state === 'busy');
  };

  const handleCloseDelete = () => {
    if (isDeleting) return;
    setDeleteTarget(null);
    setDeleteError(null);
    setCanForceDelete(false);
  };

  const handleExecuteDelete = async (force = false) => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    setDeleteError(null);

    const containerId = deleteTarget.name || deleteTarget.id;
    const res = await deleteContainer(name, containerId, force);

    if (!isSubscribedRef.current) return;
    setIsDeleting(false);

    if (res.error) {
      setDeleteError(res.error);
      // If server returned 409 Conflict, allow user to force delete
      if (res.status === 409) {
        setCanForceDelete(true);
      }
    } else {
      handleCloseDelete();
      // Instantly refresh container list
      await fetchDetails(true);
    }
  };

  const handleConfirmSuspend = async () => {
    setIsSuspending(true);
    setSuspendError(null);
    const res = await suspendPool(name);
    setIsSuspending(false);
    if (res.error) {
      setSuspendError(res.error);
    } else {
      setShowSuspendModal(false);
      await fetchDetails(true);
    }
  };

  const handleActivate = async () => {
    setIsActivating(true);
    await activatePool(name);
    setIsActivating(false);
    await fetchDetails(true);
  };

  const pool = data?.pool;
  const containers = data?.containers || [];

  // Calculate memory metrics
  let totalMemoryUsed = 0;
  let totalMemoryLimit = 0;
  let runningWithStatsCount = 0;

  for (const c of containers) {
    if (c.memory_used_bytes != null) {
      totalMemoryUsed += c.memory_used_bytes;
      runningWithStatsCount += 1;
    }
    if (c.memory_limit_bytes != null) {
      totalMemoryLimit += c.memory_limit_bytes;
    }
  }

  return (
    <div>
      <button
        className="back-link"
        onClick={() => onNavigate && onNavigate('/pools')}
      >
        &larr; Back to Manage Pools
      </button>

      {loading && (
        <div className="state-box">
          <p>Loading pool containers…</p>
        </div>
      )}

      {!loading && error && (
        <div className="state-box">
          <p style={{ color: '#c0392b' }}>Error loading pool: {error}</p>
          <button
            className="btn btn-secondary"
            onClick={() => fetchDetails(false)}
            style={{ marginTop: '1rem' }}
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && pool && (
        <>
          <div className="page-header" style={{ marginBottom: '1rem' }}>
            <div>
              <h1 className="page-title" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.6rem' }}>
                Pool: {pool.name}
                {pool.is_default && (
                  <span
                    className="status-badge"
                    style={{ background: '#e2e3e5', color: '#383d41', fontSize: '12px' }}
                  >
                    default
                  </span>
                )}
                {pool.status === 'suspended' ? (
                  pool.total > 0 ? (
                    <span className="status-badge status-building" style={{ fontSize: '12px' }}>
                      Draining
                    </span>
                  ) : (
                    <span className="status-badge status-draft" style={{ fontSize: '12px' }}>
                      Suspended
                    </span>
                  )
                ) : (
                  <span className="status-badge status-success" style={{ fontSize: '12px' }}>
                    Active
                  </span>
                )}
              </h1>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              {pool.status === 'suspended' ? (
                <button
                  className="btn btn-primary"
                  onClick={handleActivate}
                  disabled={isActivating}
                >
                  {isActivating ? 'Activating…' : 'Activate Pool'}
                </button>
              ) : (
                <button
                  className="btn btn-secondary"
                  onClick={() => { setShowSuspendModal(true); setSuspendError(null); }}
                >
                  Suspend Pool
                </button>
              )}
              <button
                className="btn btn-secondary"
                onClick={() => fetchDetails(false)}
              >
                Refresh
              </button>
            </div>
          </div>

          {/* Overview Summary Card */}
          <div className="card" style={{ marginBottom: '1.25rem' }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: '1rem',
                marginBottom: '1rem',
              }}
            >
              <div>
                <div className="meta-key">Image Tag</div>
                <div style={{ fontFamily: 'monospace', fontSize: '14px', marginTop: '4px' }}>
                  {pool.image_tag}
                </div>
              </div>
              <div>
                <div className="meta-key">Total / Ceiling</div>
                <div style={{ fontSize: '16px', fontWeight: 600, marginTop: '2px' }}>
                  {pool.total} <span style={{ color: '#888', fontWeight: 400 }}>/ {pool.max_containers}</span>
                </div>
              </div>
              <div>
                <div className="meta-key">Free (Idle)</div>
                <div style={{ fontSize: '16px', fontWeight: 600, color: pool.free > 0 ? '#155724' : '#888', marginTop: '2px' }}>
                  {pool.free}
                </div>
              </div>
              <div>
                <div className="meta-key">Occupied (Busy)</div>
                <div style={{ fontSize: '16px', fontWeight: 600, color: pool.occupied > 0 ? '#856404' : '#888', marginTop: '2px' }}>
                  {pool.occupied}
                </div>
              </div>
              <div>
                <div className="meta-key">Total Pool Memory</div>
                <div style={{ fontSize: '15px', fontWeight: 600, marginTop: '2px' }}>
                  {runningWithStatsCount > 0 ? (
                    <>
                      {formatBytes(totalMemoryUsed)}{' '}
                      <span style={{ color: '#888', fontWeight: 400, fontSize: '13px' }}>
                        / {formatBytes(totalMemoryLimit)}
                      </span>
                    </>
                  ) : (
                    '—'
                  )}
                </div>
              </div>
            </div>

            <div style={{ fontSize: '13px', color: '#666', borderTop: '1px solid #eee', paddingTop: '0.75rem' }}>
              Deleting a container does not shrink the pool. The daemon replaces it to keep the warm buffer. To shrink the pool, lower its max on the pools list.
            </div>
          </div>

          {/* Container Listing */}
          {containers.length === 0 ? (
            <div className="state-box">
              <p>No containers currently in this pool.</p>
              <p style={{ marginTop: '0.5rem', fontSize: '14px' }}>
                If max containers is greater than 0, the pool daemon will spawn warm idle containers shortly.
              </p>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Container Name</th>
                  <th>Short ID</th>
                  <th>State</th>
                  <th>Memory Usage</th>
                  <th>Started At</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {containers.map((c) => {
                  let memText = '—';
                  if (c.memory_used_bytes != null && c.memory_limit_bytes != null) {
                    const pct = Math.round((c.memory_used_bytes / c.memory_limit_bytes) * 100);
                    memText = `${formatBytes(c.memory_used_bytes)} / ${formatBytes(c.memory_limit_bytes)} (${pct}%)`;
                  } else if (c.memory_used_bytes != null) {
                    memText = formatBytes(c.memory_used_bytes);
                  }

                  return (
                    <tr key={c.id || c.name}>
                      <td style={{ fontFamily: 'monospace', fontSize: '13px' }}>
                        <strong>{c.name}</strong>
                      </td>
                      <td style={{ fontFamily: 'monospace', fontSize: '13px', color: '#555' }}>
                        {c.id || '—'}
                      </td>
                      <td>
                        <span className={`status-badge status-${c.state}`}>
                          {c.state}
                        </span>
                      </td>
                      <td style={{ fontSize: '13px' }}>
                        {memText}
                      </td>
                      <td style={{ fontSize: '13px', color: '#666' }}>
                        {formatDate(c.started_at)}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button
                          className="btn btn-danger"
                          style={{ padding: '0.25rem 0.6rem', fontSize: '13px' }}
                          onClick={() => handleOpenDelete(c)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* Container Deletion Confirmation Modal */}
      {deleteTarget && (
        <div className="modal-overlay" onClick={handleCloseDelete}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">
              {deleteTarget.state === 'busy' || canForceDelete
                ? 'Force Delete Busy Container?'
                : 'Delete Container'}
            </h2>

            <div className="modal-body">
              {deleteTarget.state === 'busy' || canForceDelete ? (
                <>
                  <div
                    style={{
                      background: '#fff3cd',
                      color: '#856404',
                      padding: '0.75rem',
                      borderRadius: '4px',
                      marginBottom: '0.75rem',
                      border: '1px solid #ffeeba',
                      fontWeight: 500,
                    }}
                  >
                    WARNING: Container is currently occupied running a job. Deleting it will kill the container and terminate the running job immediately.
                  </div>
                  <p>
                    Are you sure you want to force kill container{' '}
                    <strong style={{ fontFamily: 'monospace' }}>{deleteTarget.name}</strong> (
                    <span style={{ fontFamily: 'monospace' }}>{deleteTarget.id}</span>)?
                  </p>
                </>
              ) : (
                <>
                  <p>
                    Are you sure you want to delete container{' '}
                    <strong style={{ fontFamily: 'monospace' }}>{deleteTarget.name}</strong> (
                    <span style={{ fontFamily: 'monospace' }}>{deleteTarget.id}</span>)?
                  </p>
                  <p style={{ marginTop: '0.5rem', fontSize: '13px', color: '#666' }}>
                    The pool daemon will automatically provision a new container to maintain the warm buffer.
                  </p>
                </>
              )}

              {deleteError && (
                <div className="msg-error" style={{ marginTop: '0.75rem' }}>
                  {deleteError}
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleCloseDelete}
                disabled={isDeleting}
              >
                Cancel
              </button>
              {deleteTarget.state === 'busy' || canForceDelete ? (
                <button
                  type="button"
                  className="btn btn-danger"
                  style={{ background: '#c0392b', color: '#fff' }}
                  onClick={() => handleExecuteDelete(true)}
                  disabled={isDeleting}
                >
                  {isDeleting ? 'Force Killing…' : 'Force Delete'}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => handleExecuteDelete(false)}
                  disabled={isDeleting}
                >
                  {isDeleting ? 'Deleting…' : 'Delete Container'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Suspend Confirmation Modal */}
      {showSuspendModal && pool && (
        <div className="modal-overlay" onClick={() => !isSuspending && setShowSuspendModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Suspend Pool: {pool.name}</h2>
            <div className="modal-body">
              <p>Are you sure you want to suspend this pool?</p>
              <ul style={{ margin: '0.75rem 0', paddingLeft: '1.25rem', fontSize: '13px', lineHeight: '1.5', color: '#555' }}>
                <li>Idle containers are removed immediately.</li>
                <li>Busy containers finish their current job and are then removed.</li>
                <li>New submissions for this image will not be served until it is activated.</li>
              </ul>
              {pool.is_default && (
                <div style={{ background: '#fff3cd', color: '#856404', padding: '0.6rem 0.75rem', borderRadius: '4px', border: '1px solid #ffeeba', fontSize: '13px', marginTop: '0.75rem' }}>
                  Ticket submissions that use this image will fail while it is suspended.
                </div>
              )}
              {suspendError && <div className="msg-error" style={{ marginTop: '0.75rem' }}>{suspendError}</div>}
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowSuspendModal(false)}
                disabled={isSuspending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={handleConfirmSuspend}
                disabled={isSuspending}
              >
                {isSuspending ? 'Suspending…' : 'Suspend Pool'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
