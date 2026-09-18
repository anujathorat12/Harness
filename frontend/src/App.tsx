import { useState, type FormEvent } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext";
import type { Role } from "./types";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Sessions from "./pages/Sessions";
import SessionDetail from "./pages/SessionDetail";
import Policies from "./pages/Policies";
import PolicyDetail from "./pages/PolicyDetail";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import System from "./pages/System";

const NAV: { to: string; label: string; roles: Role[] }[] = [
  { to: "/", label: "Dashboard", roles: ["ADMIN", "OPERATOR", "AUDITOR"] },
  { to: "/agents", label: "Agents", roles: ["ADMIN", "OPERATOR"] },
  { to: "/sessions", label: "Sessions", roles: ["ADMIN", "OPERATOR", "AUDITOR", "CLIENT"] },
  { to: "/policies", label: "Policies", roles: ["ADMIN", "OPERATOR"] },
  { to: "/approvals", label: "Approvals", roles: ["ADMIN", "OPERATOR"] },
  { to: "/audit", label: "Audit", roles: ["ADMIN", "OPERATOR", "AUDITOR"] },
  { to: "/system", label: "System", roles: ["ADMIN", "OPERATOR", "AUDITOR"] },
];

function LoginScreen() {
  const { login, error } = useAuth();
  const [key, setKey] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(key.trim());
    } catch {
      // error already surfaced via useAuth().error
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-gate">
      <form className="card" onSubmit={onSubmit}>
        <h1>BYOA Harness Admin</h1>
        <p>
          Enter your API key. The backend maps it to a role (ADMIN / OPERATOR / AUDITOR / CLIENT) and enforces
          every permission server-side — this screen only decides what the UI shows you.
        </p>
        <div className="field" style={{ marginTop: 16 }}>
          <label htmlFor="api-key">X-API-Key</label>
          <input
            id="api-key"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="dev-admin-key-***CHANGE-ME***"
            autoFocus
          />
        </div>
        {error && <div className="error-banner">{error}</div>}
        <button type="submit" className="primary" disabled={submitting || !key.trim()} style={{ width: "100%" }}>
          {submitting ? "Checking…" : "Continue"}
        </button>
      </form>
    </div>
  );
}

function Shell() {
  const { principal, logout } = useAuth();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">⌁ HARNESS ADMIN</div>
        <nav>
          {NAV.filter((item) => !principal || item.roles.includes(principal.role)).map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        {principal && (
          <div className="whoami">
            <div>
              Role: <strong>{principal.role}</strong>
            </div>
            <div className="mono">{principal.label}</div>
            <button onClick={logout}>Sign out</button>
          </div>
        )}
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:id" element={<AgentDetail />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
          <Route path="/policies" element={<Policies />} />
          <Route path="/policies/:id" element={<PolicyDetail />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/system" element={<System />} />
        </Routes>
      </main>
    </div>
  );
}

function Gate() {
  const { principal, loading } = useAuth();
  if (loading) return <div className="auth-gate">Loading…</div>;
  if (!principal) return <LoginScreen />;
  return <Shell />;
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
