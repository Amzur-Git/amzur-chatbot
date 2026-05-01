import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { BrowserRouter } from "react-router-dom";
import AuthPage from "./pages/AuthPage";
import ChatPage from "./pages/ChatPage";
import { authApi } from "./lib/api";
import { useAuthStore } from "./hooks/useAuthStore";

function ProtectedRoute({ children }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  return isAuthenticated ? children : <Navigate to="/auth" replace />;
}

function PublicOnlyRoute({ children }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  return isAuthenticated ? <Navigate to="/chat" replace /> : children;
}

export default function App() {
  const [ready, setReady] = useState(false);
  const setAuth = useAuthStore((state) => state.setAuth);
  const clearAuth = useAuthStore((state) => state.clearAuth);

  useEffect(() => {
    let mounted = true;

    authApi
      .me()
      .then((user) => {
        if (!mounted) {
          return;
        }
        setAuth({
          user: {
            email: user.email,
            fullName: user.full_name ?? null,
          },
        });
      })
      .catch(() => {
        if (!mounted) {
          return;
        }
        clearAuth();
      })
      .finally(() => {
        if (mounted) {
          setReady(true);
        }
      });

    return () => {
      mounted = false;
    };
  }, [setAuth, clearAuth]);

  if (!ready) {
    return (
      <main className="auth-layout">
        <section className="auth-panel">
          <p className="muted">Checking session...</p>
        </section>
      </main>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={<Navigate to="/chat" replace />}
        />
        <Route
          path="/auth"
          element={
            <PublicOnlyRoute>
              <AuthPage />
            </PublicOnlyRoute>
          }
        />
        <Route
          path="/chat"
          element={
            <ProtectedRoute>
              <ChatPage />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
