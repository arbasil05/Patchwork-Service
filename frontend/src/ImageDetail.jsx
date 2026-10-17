import { useState, useEffect, useRef } from 'react';
import {
  getImage,
  addDependency,
  removeDependency,
  previewBuild,
  triggerBuild,
  searchNpm,
  searchPypi,
} from './api';
import { cleanLog } from './utils/logCleaner';

export default function ImageDetail({ name, onNavigate }) {
  const [image, setImage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Dependencies state
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResult, setSearchResult] = useState(null);
  const [searchError, setSearchError] = useState(null);
  const [selectedPkg, setSelectedPkg] = useState(null);
  const [selectedVersion, setSelectedVersion] = useState('');
  const [isAddingDep, setIsAddingDep] = useState(false);
  const [depError, setDepError] = useState(null);
  const [removingDep, setRemovingDep] = useState(null);

  // Build state
  const [previewData, setPreviewData] = useState(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  const [showPreview, setShowPreview] = useState(false);
  const [isTriggeringBuild, setIsTriggeringBuild] = useState(false);
  const [buildError, setBuildError] = useState(null);
  const [copiedLog, setCopiedLog] = useState(false);

  const searchDebounceRef = useRef(null);

  // Fetch initial image details
  const loadImage = async (imgName) => {
    setLoading(true);
    setError(null);
    const { data, error: err } = await getImage(imgName);
    if (err) {
      setError(err);
    } else {
      setImage(data);
    }
    setLoading(false);
  };

  useEffect(() => {
    if (name) {
      loadImage(name);
    }
  }, [name]);

  // Polling for build status: runs when status is queued or building
  useEffect(() => {
    if (!name) return;
    if (image && image.status !== 'queued' && image.status !== 'building') return;

    let isSubscribed = true;

    const poll = async () => {
      const { data, error: err } = await getImage(name);
      if (!isSubscribed) return;

      if (!err && data) {
        setImage(data);
      }
    };

    // Poll every 2 seconds
    const intervalId = setInterval(poll, 2000);

    // Immediately poll as soon as user switches back to this browser tab
    const handleActive = () => {
      if (document.visibilityState === 'visible') {
        poll();
      }
    };

    window.addEventListener('focus', handleActive);
    document.addEventListener('visibilitychange', handleActive);

    return () => {
      isSubscribed = false;
      clearInterval(intervalId);
      window.removeEventListener('focus', handleActive);
      document.removeEventListener('visibilitychange', handleActive);
    };
  }, [name, image?.status]);

  // Debounced search for npm / pypi
  useEffect(() => {
    if (searchDebounceRef.current) {
      clearTimeout(searchDebounceRef.current);
    }

    const trimmed = searchQuery.trim();
    if (!trimmed) {
      setSearchResult(null);
      setSearchError(null);
      setIsSearching(false);
      return;
    }

    setIsSearching(true);
    setSearchError(null);

    searchDebounceRef.current = setTimeout(async () => {
      if (!image) return;
      const searchFn = image.runtime === 'node' ? searchNpm : searchPypi;
      const { data, error: err } = await searchFn(trimmed);

      setIsSearching(false);
      if (err) {
        setSearchError(err);
        setSearchResult(null);
      } else {
        setSearchResult(data);
      }
    }, 300);

    return () => {
      if (searchDebounceRef.current) {
        clearTimeout(searchDebounceRef.current);
      }
    };
  }, [searchQuery, image?.runtime]);

  // Handle selecting a package from search
  const handleSelectPackage = (pkgData) => {
    setSelectedPkg(pkgData);
    setSelectedVersion(pkgData.latest_version || (pkgData.versions && pkgData.versions[0]) || '');
    setSearchResult(null);
    setSearchQuery('');
    setDepError(null);
  };

  // Handle adding dependency
  const handleAddDependencySubmit = async (e) => {
    e.preventDefault();
    if (!selectedPkg || !image) return;

    setIsAddingDep(true);
    setDepError(null);

    const version = selectedVersion.trim() || undefined;
    const { data, error: err } = await addDependency(image.name, selectedPkg.name, version);
    setIsAddingDep(false);

    if (err) {
      setDepError(err);
    } else {
      setImage(data);
      setSelectedPkg(null);
      setSelectedVersion('');
    }
  };

  // Handle removing dependency
  const handleRemoveDependency = async (pkgName) => {
    if (!image) return;
    setRemovingDep(pkgName);
    setDepError(null);

    const { data, error: err } = await removeDependency(image.name, pkgName);
    setRemovingDep(null);

    if (err) {
      setDepError(err);
    } else {
      setImage(data);
    }
  };

  // Handle preview build
  const handleTogglePreview = async () => {
    if (showPreview) {
      setShowPreview(false);
      return;
    }

    if (!image) return;
    setIsPreviewLoading(true);
    setPreviewError(null);

    const { data, error: err } = await previewBuild(image.name);
    setIsPreviewLoading(false);

    if (err) {
      setPreviewError(err);
    } else {
      setPreviewData(data);
      setShowPreview(true);
    }
  };

  // Handle trigger build
  const handleTriggerBuild = async () => {
    if (!image) return;
    setIsTriggeringBuild(true);
    setBuildError(null);

    const { data, error: err } = await triggerBuild(image.name);
    setIsTriggeringBuild(false);

    if (err) {
      setBuildError(err);
    } else {
      setImage(data);
    }
  };

  // Handle copy build log
  const handleCopyLog = () => {
    if (!image?.build_log) return;
    const text = cleanLog(image.build_log);
    navigator.clipboard.writeText(text).then(() => {
      setCopiedLog(true);
      setTimeout(() => setCopiedLog(false), 2000);
    });
  };

  if (loading) {
    return (
      <div>
        <button className="back-link" onClick={() => onNavigate('/images')}>
          &larr; Back to Manage Images
        </button>
        <div className="state-box">
          <p>Loading image definition &ldquo;{name}&rdquo;…</p>
        </div>
      </div>
    );
  }

  if (error || !image) {
    return (
      <div>
        <button className="back-link" onClick={() => onNavigate('/images')}>
          &larr; Back to Manage Images
        </button>
        <div className="state-box">
          <p style={{ color: '#c0392b' }}>
            {error || `Image definition "${name}" not found.`}
          </p>
          <button
            className="btn btn-secondary"
            onClick={() => loadImage(name)}
            style={{ marginTop: '1rem' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const isBuilding = image.status === 'queued' || image.status === 'building';
  const cleanedLog = image.build_log ? cleanLog(image.build_log) : '';

  return (
    <div>
      <button className="back-link" onClick={() => onNavigate('/images')}>
        &larr; Back to Manage Images
      </button>

      {/* ── Section 1: Header ────────────────────────────────────────────── */}
      <div className="card">
        <div className="page-header" style={{ marginBottom: '1rem' }}>
          <h1 className="page-title">{image.name}</h1>
          <div>
            <span className={`status-badge status-${image.status}`}>
              {image.status}
              {isBuilding && <span className="building-pulse" style={{ marginLeft: '4px' }}>●</span>}
            </span>
            {image.needs_rebuild && (
              <span className="status-badge" style={{ backgroundColor: '#fff3cd', color: '#856404', marginLeft: '8px' }}>
                Rebuild needed
              </span>
            )}
          </div>
        </div>

        <div className="meta-grid">
          <span className="meta-key">Runtime:</span>
          <span>{image.runtime}</span>

          <span className="meta-key">Status:</span>
          <span>
            {image.status}
            {isBuilding && (
              <span style={{ color: '#004085', marginLeft: '6px', fontSize: '13px' }}>
                ({image.status === 'queued' ? 'queued in background…' : 'building Docker image…'})
              </span>
            )}
            {image.needs_rebuild && (
              <span style={{ color: '#856404', marginLeft: '6px', fontSize: '13px' }}>
                (rebuild needed for updated test framework)
              </span>
            )}
          </span>

          <span className="meta-key">Image Tag:</span>
          <span style={{ fontFamily: 'monospace' }}>
            {image.image_tag || '—'}
          </span>

          <span className="meta-key">Created At:</span>
          <span>
            {image.created_at ? new Date(image.created_at).toLocaleString() : '—'}
          </span>

          <span className="meta-key">Updated At:</span>
          <span>
            {image.updated_at ? new Date(image.updated_at).toLocaleString() : '—'}
          </span>
        </div>
      </div>

      {/* ── Section 2: Dependencies ──────────────────────────────────────── */}
      <div className="card">
        <h2 className="card-title">Dependencies</h2>

        {depError && <div className="msg-error">{depError}</div>}

        <ul className="dep-list" style={{ marginBottom: (!image.dependencies || image.dependencies.length === 0) ? '1rem' : 0 }}>
          {/* Fixed Infrastructure Dependencies */}
          {image.fixed_dependencies && image.fixed_dependencies.map((fixed) => (
            <li key={fixed.name} className="dep-item" style={{ backgroundColor: '#fcfcfc', borderLeft: '3px solid #007bff' }}>
              <div>
                <span className="dep-name">{fixed.name}</span>
                <span className="dep-version">v{fixed.version}</span>
                <span
                  style={{
                    marginLeft: '8px',
                    fontSize: '11px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    backgroundColor: '#e9ecef',
                    color: '#495057',
                    fontWeight: 500
                  }}
                >
                  Included in every image
                </span>
              </div>
            </li>
          ))}

          {/* Admin Configured Dependencies */}
          {image.dependencies && image.dependencies.map((dep) => (
            <li key={dep.name} className="dep-item">
              <div>
                <span className="dep-name">{dep.name}</span>
                <span className="dep-version">v{dep.version}</span>
              </div>
              <button
                className="btn btn-danger"
                style={{ padding: '0.25rem 0.6rem', fontSize: '13px' }}
                onClick={() => handleRemoveDependency(dep.name)}
                disabled={removingDep === dep.name}
              >
                {removingDep === dep.name ? 'Removing…' : 'Remove'}
              </button>
            </li>
          ))}
        </ul>

        {(!image.dependencies || image.dependencies.length === 0) && (
          <p style={{ color: '#888', fontSize: '13px', marginBottom: '1.25rem' }}>
            No additional user dependencies added yet.
          </p>
        )}

        {/* Add Dependency Search Box */}
        <div style={{ marginTop: '1.25rem', paddingTop: '1rem', borderTop: '1px solid #eee' }}>
          <label className="form-label">
            Add Dependency ({image.runtime === 'node' ? 'NPM' : 'PyPI'})
          </label>

          <div className="search-wrap">
            <input
              type="text"
              className="form-input"
              placeholder={
                image.runtime === 'node'
                  ? 'Search npm package (e.g. express, @types/node)…'
                  : 'Search PyPI package (e.g. requests, pydantic)…'
              }
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              disabled={isAddingDep}
            />

            {isSearching && (
              <div
                style={{
                  position: 'absolute',
                  right: '10px',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  fontSize: '12px',
                  color: '#888',
                }}
              >
                Searching…
              </div>
            )}

            {searchError && (
              <div className="msg-error" style={{ marginTop: '0.5rem' }}>
                {searchError}
              </div>
            )}

            {searchResult && (
              <div className="search-results">
                {searchResult.exists ? (
                  <div
                    className="search-result-item"
                    onClick={() => handleSelectPackage(searchResult)}
                  >
                    <span className="search-result-name">{searchResult.name}</span>
                    <span className="search-result-version">
                      (latest: {searchResult.latest_version || 'unknown'})
                    </span>
                  </div>
                ) : (
                  <div
                    className="search-result-item"
                    style={{ color: '#888', cursor: 'default' }}
                  >
                    Package &ldquo;{searchResult.name}&rdquo; not found in {image.runtime} registry.
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Selected package version picker form */}
          {selectedPkg && (
            <form onSubmit={handleAddDependencySubmit} style={{ marginTop: '0.75rem' }}>
              <div style={{ fontSize: '14px', marginBottom: '0.5rem' }}>
                Selected package: <strong>{selectedPkg.name}</strong>
              </div>

              <div className="version-row">
                <div style={{ flex: 1, minWidth: '180px' }}>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="Version (e.g. 1.0.0)"
                    value={selectedVersion}
                    onChange={(e) => setSelectedVersion(e.target.value)}
                    disabled={isAddingDep}
                  />
                </div>

                {selectedPkg.versions && selectedPkg.versions.length > 0 && (
                  <div style={{ flex: 1, minWidth: '180px' }}>
                    <select
                      className="form-select"
                      value={selectedPkg.versions.includes(selectedVersion) ? selectedVersion : ''}
                      onChange={(e) => {
                        if (e.target.value) setSelectedVersion(e.target.value);
                      }}
                      disabled={isAddingDep}
                    >
                      <option value="">— Choose recent version —</option>
                      {selectedPkg.versions.map((ver) => (
                        <option key={ver} value={ver}>
                          {ver} {ver === selectedPkg.latest_version ? '(latest)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isAddingDep}
                >
                  {isAddingDep ? 'Adding…' : 'Add Dependency'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    setSelectedPkg(null);
                    setSelectedVersion('');
                    setDepError(null);
                  }}
                  disabled={isAddingDep}
                >
                  Cancel
                </button>
              </div>
              <span style={{ fontSize: '12px', color: '#888', display: 'block', marginTop: '4px' }}>
                Leave version as is for latest, choose a recent version, or type a specific version.
              </span>
            </form>
          )}
        </div>
      </div>

      {/* ── Section 3: Build ────────────────────────────────────────────── */}
      <div className="card">
        <h2 className="card-title">Build</h2>

        {buildError && (
          <div className="msg-error">
            <strong>Build request error:</strong> {buildError}
          </div>
        )}

        {image.status === 'failed' && (
          <div className="msg-error" style={{ marginBottom: '1rem' }}>
            <strong>Build Failed!</strong> The Docker build failed. Please inspect the build log below.
          </div>
        )}

        {isBuilding && (
          <div className="msg-info" style={{ marginBottom: '1rem' }}>
            <span className="building-pulse" style={{ marginRight: '6px' }}>●</span>
            Build is currently <strong>{image.status}</strong>. Polling for updates…
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1.25rem' }}>
          <button
            className="btn btn-primary"
            onClick={handleTriggerBuild}
            disabled={isBuilding || isTriggeringBuild}
          >
            {isTriggeringBuild
              ? 'Starting Build…'
              : isBuilding
              ? `Building (${image.status})…`
              : 'Build Image'}
          </button>

          <button
            className="btn btn-secondary"
            onClick={handleTogglePreview}
            disabled={isPreviewLoading}
          >
            {isPreviewLoading
              ? 'Loading Preview…'
              : showPreview
              ? 'Hide Preview'
              : 'Preview Build'}
          </button>
        </div>

        {/* Preview Output */}
        {showPreview && (
          <div style={{ marginBottom: '1.5rem', padding: '1rem', background: '#fafafa', borderRadius: '4px', border: '1px solid #eee' }}>
            <h3 style={{ fontSize: '14px', margin: '0 0 0.5rem', color: '#444' }}>Build Context Preview</h3>
            {previewError && <div className="msg-error">{previewError}</div>}
            {previewData && (
              <div>
                <label className="form-label" style={{ marginTop: '0.5rem' }}>
                  Dockerfile
                </label>
                <pre className="preview-block">{previewData.dockerfile}</pre>

                <label className="form-label" style={{ marginTop: '0.75rem' }}>
                  {previewData.dependency_file_name || 'Dependencies file'}
                </label>
                <pre className="preview-block">
                  {previewData.dependency_file_content || '# (No dependencies)'}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* Build Log */}
        {cleanedLog ? (
          <div>
            <div className="log-copy-row">
              <span style={{ marginRight: 'auto', fontSize: '13px', fontWeight: 600, color: '#555', alignSelf: 'center' }}>
                Build Output Log
              </span>
              <button
                className="btn btn-secondary"
                style={{ padding: '0.25rem 0.6rem', fontSize: '12px' }}
                onClick={handleCopyLog}
              >
                {copiedLog ? 'Copied!' : 'Copy Log'}
              </button>
            </div>
            <pre className="log-block">{cleanedLog}</pre>
          </div>
        ) : (
          <p style={{ color: '#888', fontSize: '14px', margin: 0 }}>
            No build log available yet. Click &ldquo;Build Image&rdquo; to start a build.
          </p>
        )}
      </div>
    </div>
  );
}
