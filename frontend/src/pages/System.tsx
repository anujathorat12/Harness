import { api } from "../api";
import { useApiData } from "../hooks";
import { ErrorBanner, Loading } from "../components";

export default function System() {
  const { data, loading, error, reload } = useApiData(() => api.systemStatus());

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>System</h1>
          <p>Operational status. Never shows API key values — only counts, flags, and non-secret configuration.</p>
        </div>
        <button onClick={reload}>Refresh</button>
      </div>
      <ErrorBanner message={error} />
      {loading && <Loading />}
      {data && (
        <div className="grid cols-2">
          <div className="card">
            <h3>Service</h3>
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>Environment</dt>
              <dd className="mono">{data.environment}</dd>
              <dt>API version</dt>
              <dd className="mono">{data.api_version}</dd>
              <dt>Database</dt>
              <dd className={data.database === "ok" ? "" : "mono"} style={{ color: data.database === "ok" ? "var(--allow)" : "var(--deny)" }}>
                {data.database}
              </dd>
            </dl>
          </div>
          <div className="card">
            <h3>Concurrency</h3>
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>Sessions running now</dt>
              <dd>{data.sessions_running_now}</dd>
              <dt>Max per agent</dt>
              <dd>{data.max_concurrent_sessions_per_agent}</dd>
              <dt>Max total</dt>
              <dd>{data.max_concurrent_sessions_total}</dd>
            </dl>
          </div>
          <div className="card" style={{ gridColumn: "1 / -1" }}>
            <h3>Sandbox runtimes</h3>
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>ProcessRuntime</dt>
              <dd>available (this deployment's default; CPU/memory/PID/time limits enforced)</dd>
              <dt>DeclarativeRuntime</dt>
              <dd>available (no code execution — pure data interpretation)</dd>
              <dt>ContainerRuntime</dt>
              <dd style={{ color: data.container_runtime_available ? "var(--allow)" : "var(--muted)" }}>
                {data.container_runtime_available ? "available" : "unavailable"} — {data.container_runtime_note}
              </dd>
            </dl>
          </div>
        </div>
      )}
    </div>
  );
}
