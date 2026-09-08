import { useAuth } from './AuthContext';

export default function Shell() {
  const { logout } = useAuth();

  return (
    <div style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
      <h1>Hi admin</h1>
      <button onClick={logout} style={{ padding: '0.5rem 1rem', cursor: 'pointer' }}>
        Logout
      </button>
    </div>
  );
}
