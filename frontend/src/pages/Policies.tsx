import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { api, ApiError } from "../api";
import { useApiData } from "../hooks";
import { Empty, ErrorBanner, Loading, fmtTime } from "../components";

const TEMPLATE = `policy:
  name: my-policy
  version: 1
  default_effect: deny

rules:
  - id: allow-workspace-read
    action: file.read
    resource: "/workspace/**"
    effect: allow

  - id: approve-email
    action: email.send
    effect: require_approval
`;

function CreatePolicyForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [source, setSource] = useState(TEMPLATE);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.createPolicy({ name, source });
      setName("");
      setSource(TEMPLATE);
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button className="primary" onClick={() => setOpen(true)}>
        + Create policy
      </button>
    );
  }

  return (
    <form className="card" onSubmit={onSubmit} style={{ marginBottom: 20 }}>
      <h3 style={{ marginBottom: 12 }}>Create a new policy</h3>
      <p className="muted small" style={{ marginTop: -6, marginBottom: 12 }}>
        Raw YAML/JSON, exactly what the backend Policy Engine parses — this form does not add a separate policy
        language of its own. Validation and versioning are entirely backend-authoritative.
      </p>
      <ErrorBanner message={error} />
      <div className="field">
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div className="field">
        <label>Source (YAML)</label>
        <textarea value={source} onChange={(e) => setSource(e.target.value)} />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Creating…" : "Create"}
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function Policies() {
  const { principal } = useAuth();
  const canCreate = principal?.role === "ADMIN";
  const { data, loading, error, reload } = useApiData(() => api.listPolicies());

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Policies</h1>
          <p>Immutable, versioned rulebooks. Editing creates a new version — nothing is ever overwritten.</p>
        </div>
      </div>
      <ErrorBanner message={error} />
      {canCreate && <CreatePolicyForm onCreated={reload} />}
      <div style={{ height: 16 }} />
      {loading && <Loading />}
      {data && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Versions</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 && (
                <tr>
                  <td colSpan={3}>
                    <Empty>No policies created yet.</Empty>
                  </td>
                </tr>
              )}
              {data.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={`/policies/${p.id}`}>{p.name}</Link>
                  </td>
                  <td>{p.versions.length}</td>
                  <td className="small muted">{fmtTime(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
