import { Link } from "react-router-dom";
import { api } from "../api";
import { useApiData } from "../hooks";
import { ErrorBanner, Loading, Pill, fmtTime, shortId } from "../components";

export default function Dashboard() {
  const { data, loading, error, reload } = useApiData(() => api.dashboardSummary());

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>Platform-wide view, computed live from the same durable rows every other page reads.</p>
        </div>
        <button onClick={reload}>Refresh</button>
      </div>
      <ErrorBanner message={error} />
      {loading && <Loading />}
      {data && (
        <>
          <div className="grid cols-4 section">
            <div className="card">
              <div className="stat-label">Agents registered</div>
              <div className="stat">{data.agents_total}</div>
            </div>
            <div className="card">
              <div className="stat-label">Sessions running</div>
              <div className="stat">{data.sessions_running}</div>
            </div>
            <div className="card">
              <div className="stat-label">Sessions completed</div>
              <div className="stat">{data.sessions_completed}</div>
            </div>
            <div className="card">
              <div className="stat-label">Sessions failed</div>
              <div className="stat">{data.sessions_failed}</div>
            </div>
          </div>

          <div className="grid cols-2 section">
            <div className="card">
              <div className="stat-label">Approvals pending human review</div>
              <div className="stat">{data.approvals_pending}</div>
              {data.approvals_pending > 0 && (
                <p className="small" style={{ marginTop: 8 }}>
                  <Link to="/approvals">Go resolve them →</Link>
                </p>
              )}
            </div>
          </div>

          <div className="section">
            <h2>Recent policy decisions</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Type</th>
                    <th>Resource</th>
                    <th>Decision</th>
                    <th>Rule</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_actions.length === 0 && (
                    <tr>
                      <td colSpan={6} className="empty-state">
                        No governed actions yet — submit a task to see policy decisions appear here.
                      </td>
                    </tr>
                  )}
                  {data.recent_actions.map((a) => (
                    <tr key={a.id}>
                      <td className="mono">{shortId(a.id)}</td>
                      <td className="mono">{a.action_type}</td>
                      <td className="mono">{a.resource ?? "—"}</td>
                      <td>
                        <Pill value={a.decision} />
                      </td>
                      <td className="mono small">{a.rule_id ?? "—"}</td>
                      <td className="small muted">{fmtTime(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="section">
            <h2>Recent security / audit events</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Actor</th>
                    <th>Decision</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_audit_events.length === 0 && (
                    <tr>
                      <td colSpan={4} className="empty-state">
                        Nothing logged yet.
                      </td>
                    </tr>
                  )}
                  {data.recent_audit_events.map((e) => (
                    <tr key={e.id}>
                      <td className="mono">{e.event_type}</td>
                      <td className="mono small">{e.actor}</td>
                      <td>
                        <Pill value={e.decision} />
                      </td>
                      <td className="small muted">{fmtTime(e.timestamp)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
