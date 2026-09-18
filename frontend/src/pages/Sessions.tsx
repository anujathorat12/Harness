import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApiData } from "../hooks";
import { Empty, ErrorBanner, Loading, Pill, fmtDuration, fmtTime, shortId } from "../components";

const STATUSES = ["", "pending", "running", "completed", "failed"];

export default function Sessions() {
  const [status, setStatus] = useState("");
  const { data, loading, error, reload } = useApiData(
    () => api.listSessions(status ? { status } : {}),
    [status]
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Sessions</h1>
          <p>One row per isolated agent execution — one sandbox, one policy binding, one lifetime.</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "All statuses"}
              </option>
            ))}
          </select>
          <button onClick={reload}>Refresh</button>
        </div>
      </div>
      <ErrorBanner message={error} />
      {loading && <Loading />}
      {data && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Session</th>
                <th>Agent</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 && (
                <tr>
                  <td colSpan={5}>
                    <Empty>No sessions yet — submit a task to an agent to create one.</Empty>
                  </td>
                </tr>
              )}
              {data.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/sessions/${s.id}`} className="mono">
                      {shortId(s.id)}
                    </Link>
                  </td>
                  <td>
                    <Link to={`/agents/${s.agent_id}`} className="mono small">
                      {shortId(s.agent_id)}
                    </Link>
                  </td>
                  <td>
                    <Pill value={s.status} />
                  </td>
                  <td className="small">{fmtDuration(s.started_at, s.ended_at)}</td>
                  <td className="small muted">{fmtTime(s.started_at ?? s.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
