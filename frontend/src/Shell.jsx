import { useState, useEffect } from 'react';
import { useAuth } from './AuthContext';
import ImageList from './ImageList';
import ImageDetail from './ImageDetail';
import './admin.css';

export default function Shell() {
  const { logout } = useAuth();
  const [currentPath, setCurrentPath] = useState(() => {
    const p = window.location.pathname;
    return p === '/' || p === '' ? '/images' : p;
  });

  useEffect(() => {
    if (window.location.pathname === '/' || window.location.pathname === '') {
      window.history.replaceState({}, '', '/images');
    }
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname);
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = (path) => {
    if (window.location.pathname !== path) {
      window.history.pushState({}, '', path);
      setCurrentPath(path);
    }
  };

  // Route matching: /images/:name
  const detailMatch = currentPath.match(/^\/images\/(.+)$/);
  const detailImageName = detailMatch ? decodeURIComponent(detailMatch[1]) : null;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-title">Patchwork Admin</div>
        <nav style={{ flex: 1 }}>
          <button
            className="sidebar-nav-item active"
            onClick={() => navigate('/images')}
          >
            Manage Images
          </button>
        </nav>
        <div className="sidebar-footer">
          <button className="btn-logout" onClick={logout}>
            Logout
          </button>
        </div>
      </aside>

      <main className="shell-main">
        {detailImageName ? (
          <ImageDetail name={detailImageName} onNavigate={navigate} />
        ) : (
          <ImageList onNavigate={navigate} />
        )}
      </main>
    </div>
  );
}
