import { AuthProvider, useAuth } from './AuthContext';
import Login from './Login';
import Shell from './Shell';

function AppContent() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return <div style={{ padding: '2rem', fontFamily: 'sans-serif' }}>Loading...</div>;
  }

  return isAuthenticated ? <Shell /> : <Login />;
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;
