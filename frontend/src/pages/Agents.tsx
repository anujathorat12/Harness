import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { load as loadYaml } from "js-yaml";
import { useAuth } from "../AuthContext";
import { api, ApiError } from "../api";
import { useApiData } from "../hooks";
import { Empty, ErrorBanner, Loading, Pill, fmtTime } from "../components";

const DECLARATIVE_TEMPLATE = `name: my-declarative-agent
steps:
  - action: file.read
    resource: "/workspace/{{input.source_file}}"
    parameters: {}
`;

function RegisterAgentForm({ onRegistered }: { onRegistered: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [shape, setShape] = useState<"code" | "declarative">("declarative");
  const [entrypoint, setEntrypoint] = useState("python3 harness/examples_agents/code_agent/agent.py");
  const [spec, setSpec] = useState(DECLARATIVE_TEMPLATE);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (shape === "declarative") {
        const parsedSpec = loadYaml(spec);
        await api.registerAgent({
          name,
          owner,
          shape: "declarative",
          initial_version: { runtime_kind: "declarative", spec: parsedSpec, resource_limits: {} },
        });
      } else {
        await api.registerAgent({
          name,
          owner,
          shape: "code",
          initial_version: {
            runtime_kind: "process",
            spec: { entrypoint: entrypoint.split(/\s+/) },
            resource_limits: { cpu_seconds: 5, memory_mb: 128, timeout_seconds: 15 },
          },
        });
      }
      setName("");
      setOwner("");
      setOpen(false);
      onRegistered();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button className="primary" onClick={() => setOpen(true)}>
        + Register agent
      </button>
    );
  }

  return (
    <form className="card" onSubmit={onSubmit} style={{ marginBottom: 20 }}>
      <h3 style={{ marginBottom: 12 }}>Register a new agent</h3>
      <ErrorBanner message={error} />
      <div className="split">
        <div className="field">
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field">
          <label>Owner</label>
          <input value={owner} onChange={(e) => setOwner(e.target.value)} required />
        </div>
      </div>
      <div className="field">
        <label>Shape</label>
        <select value={shape} onChange={(e) => setShape(e.target.value as "code" | "declarative")}>
          <option value="declarative">Declarative (data only, no code executed)</option>
          <option value="code">Code (real sandboxed process)</option>
        </select>
      </div>
      {shape === "declarative" ? (
        <div className="field">
          <label>Steps (YAML)</label>
          <textarea value={spec} onChange={(e) => setSpec(e.target.value)} />
        </div>
      ) : (
        <div className="field">
          <label>Entrypoint (argv, space-separated)</label>
          <input value={entrypoint} onChange={(e) => setEntrypoint(e.target.value)} />
        </div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Registering…" : "Register"}
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function Agents() {
  const { principal } = useAuth();
  const canRegister = principal?.role === "ADMIN";
  const { data, loading, error, reload } = useApiData(() => api.listAgents());

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Agents</h1>
          <p>Every registered BYOA agent, either shape, across every runtime.</p>
        </div>
      </div>
      <ErrorBanner message={error} />
      {canRegister && <RegisterAgentForm onRegistered={reload} />}
      <div style={{ height: 16 }} />
      {loading && <Loading />}
      {data && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Shape</th>
                <th>Owner</th>
                <th>Latest version</th>
                <th>Runtime</th>
                <th>Registered</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <Empty>No agents registered yet.</Empty>
                  </td>
                </tr>
              )}
              {data.map((agent) => {
                const latest = agent.versions[agent.versions.length - 1];
                return (
                  <tr key={agent.id}>
                    <td>
                      <Link to={`/agents/${agent.id}`}>{agent.name}</Link>
                    </td>
                    <td>
                      <Pill value={agent.shape} />
                    </td>
                    <td className="muted">{agent.owner}</td>
                    <td>v{latest?.version ?? "—"}</td>
                    <td className="mono small">{latest?.runtime_kind ?? "—"}</td>
                    <td className="small muted">{fmtTime(agent.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
