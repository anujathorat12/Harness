import { useState, type FormEvent } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { api, ApiError } from "../api";
import { useApiData } from "../hooks";
import { ErrorBanner, Loading, Pill, fmtTime } from "../components";

function SubmitTaskForm({ agentId }: { agentId: string }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('{\n  "report_path": "report.txt",\n  "notify": "team@example.com"\n}');
  const [workspaceFiles, setWorkspaceFiles] = useState('{\n  "report.txt": "Q3 revenue up 12%."\n}');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const parsedInput = input.trim() ? JSON.parse(input) : {};
      const parsedFiles = workspaceFiles.trim() ? JSON.parse(workspaceFiles) : {};
      const result = await api.submitTask({ agent_id: agentId, input: parsedInput, workspace_files: parsedFiles });
      navigate(`/sessions/${result.session_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button className="primary" onClick={() => setOpen(true)}>
        Submit task
      </button>
    );
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h3 style={{ marginBottom: 12 }}>Submit a task to this agent</h3>
      <ErrorBanner message={error} />
      <div className="field">
        <label>Task input (JSON)</label>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} style={{ minHeight: 100 }} />
      </div>
      <div className="field">
        <label>Seed workspace files (JSON, optional — path → contents)</label>
        <textarea value={workspaceFiles} onChange={(e) => setWorkspaceFiles(e.target.value)} style={{ minHeight: 100 }} />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Submitting…" : "Submit"}
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function AgentDetail() {
  const { id } = useParams<{ id: string }>();
  const { principal } = useAuth();
  const canSubmit = principal && ["ADMIN", "OPERATOR", "CLIENT"].includes(principal.role);
  const { data: agent, loading, error } = useApiData(() => api.getAgent(id!), [id]);
  const { data: attachment } = useApiData(() => api.lookupAttachment("agent", id!), [id]);

  if (loading) return <Loading />;
  if (error) return <ErrorBanner message={error} />;
  if (!agent) return null;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{agent.name}</h1>
          <p>
            <Pill value={agent.shape} /> owned by <span className="mono">{agent.owner}</span>
          </p>
        </div>
        <Link to="/agents">← Back to agents</Link>
      </div>

      <div className="section">
        <h2>Attached policy</h2>
        <div className="card">
          {attachment ? (
            <dl className="kv">
              <dt>Policy</dt>
              <dd>
                <Link to={`/policies/${attachment.policy_id}`}>{attachment.policy_name}</Link>
              </dd>
              <dt>Version</dt>
              <dd>v{attachment.version}</dd>
            </dl>
          ) : (
            <p className="muted">
              No policy attached to this agent — every action it attempts will be <strong>denied</strong> (fail
              closed). Attach one from the Policies page.
            </p>
          )}
        </div>
      </div>

      <div className="section">
        <h2>Versions</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Runtime</th>
                <th>Resource limits</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {agent.versions.map((v) => (
                <tr key={v.id}>
                  <td>v{v.version}</td>
                  <td>
                    <Pill value={v.status} />
                  </td>
                  <td className="mono">{v.runtime_kind}</td>
                  <td className="mono small">{JSON.stringify(v.resource_limits)}</td>
                  <td className="small muted">{fmtTime(v.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {canSubmit && (
        <div className="section">
          <h2>Run it</h2>
          <SubmitTaskForm agentId={agent.id} />
        </div>
      )}
    </div>
  );
}
