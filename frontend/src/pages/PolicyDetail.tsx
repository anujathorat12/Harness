import { useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { api, ApiError } from "../api";
import { useApiData } from "../hooks";
import { ErrorBanner, Loading, fmtTime } from "../components";

function AttachForm({ policyId, latestVersion }: { policyId: string; latestVersion: number }) {
  const [scopeType, setScopeType] = useState<"agent" | "session">("agent");
  const [scopeId, setScopeId] = useState("");
  const [version, setVersion] = useState(latestVersion);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    setSubmitting(true);
    try {
      await api.attachPolicy(policyId, version, scopeType, scopeId.trim());
      setStatus(`Attached v${version} to ${scopeType} ${scopeId.trim()}.`);
      setScopeId("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h3 style={{ marginBottom: 12 }}>Attach a version</h3>
      <ErrorBanner message={error} />
      {status && <p className="small" style={{ color: "var(--allow)" }}>{status}</p>}
      <div className="split">
        <div className="field">
          <label>Scope</label>
          <select value={scopeType} onChange={(e) => setScopeType(e.target.value as "agent" | "session")}>
            <option value="agent">Agent (default for all its sessions)</option>
            <option value="session">Session (overrides the agent default)</option>
          </select>
        </div>
        <div className="field">
          <label>Version</label>
          <input type="number" value={version} min={1} onChange={(e) => setVersion(Number(e.target.value))} />
        </div>
      </div>
      <div className="field">
        <label>{scopeType === "agent" ? "Agent ID" : "Session ID"}</label>
        <input value={scopeId} onChange={(e) => setScopeId(e.target.value)} required className="mono" />
      </div>
      <button type="submit" className="primary" disabled={submitting}>
        {submitting ? "Attaching…" : "Attach"}
      </button>
    </form>
  );
}

function AddVersionForm({ policyId, nextVersion, onAdded }: { policyId: string; nextVersion: number; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState(
    `policy:\n  name: my-policy\n  version: ${nextVersion}\n  default_effect: deny\n\nrules:\n  - id: allow-workspace-read\n    action: file.read\n    resource: "/workspace/**"\n    effect: allow\n`
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.addPolicyVersion(policyId, source);
      setOpen(false);
      onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}>+ Add new version (v{nextVersion})</button>
    );
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <ErrorBanner message={error} />
      <div className="field">
        <label>Source (YAML) — version field must be {nextVersion}</label>
        <textarea value={source} onChange={(e) => setSource(e.target.value)} />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Saving…" : "Save new version"}
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function PolicyDetail() {
  const { id } = useParams<{ id: string }>();
  const { principal } = useAuth();
  const isAdmin = principal?.role === "ADMIN";
  const { data: policy, loading, error, reload } = useApiData(() => api.getPolicy(id!), [id]);

  if (loading) return <Loading />;
  if (error) return <ErrorBanner message={error} />;
  if (!policy) return null;

  const latest = policy.versions[policy.versions.length - 1];

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{policy.name}</h1>
          <p>{policy.versions.length} version(s), immutable once created.</p>
        </div>
        <Link to="/policies">← Back to policies</Link>
      </div>

      <div className="section">
        <h2>
          Version {latest?.version} <span className="muted small">(current)</span>
        </h2>
        <div className="card">
          <pre style={{ fontSize: 12.5, overflowX: "auto" }}>{JSON.stringify(latest?.document, null, 2)}</pre>
        </div>
      </div>

      {policy.versions.length > 1 && (
        <div className="section">
          <h2>Version history</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Rules</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {[...policy.versions].reverse().map((v) => (
                  <tr key={v.id}>
                    <td>v{v.version}</td>
                    <td>{Array.isArray((v.document as { rules?: unknown[] })?.rules) ? (v.document as { rules: unknown[] }).rules.length : "—"}</td>
                    <td className="small muted">{fmtTime(v.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="section">
          <h2>Manage</h2>
          <div className="split">
            <AttachForm policyId={policy.id} latestVersion={latest?.version ?? 1} />
            <AddVersionForm policyId={policy.id} nextVersion={(latest?.version ?? 0) + 1} onAdded={reload} />
          </div>
        </div>
      )}
    </div>
  );
}
