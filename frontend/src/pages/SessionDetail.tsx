import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import { useApiData } from "../hooks";
import { ErrorBanner, Loading, Pill, fmtDuration, fmtTime, shortId } from "../components";

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: session, loading, error } = useApiData(() => api.getSession(id!), [id]);
  const { data: tasks } = useApiData(() => api.listTasks({ session_id: id! }), [id]);
  const task = tasks?.[0];
  const { data: actions } = useApiData(
    () => (task ? api.listTaskActions(task.id) : Promise.resolve([])),
    [task?.id]
  );

  if (loading) return <Loading />;
  if (error) return <ErrorBanner message={error} />;
  if (!session) return null;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="mono">{shortId(session.id)}</h1>
          <p>
            <Pill value={session.status} /> — agent{" "}
            <Link to={`/agents/${session.agent_id}`} className="mono">
              {shortId(session.agent_id)}
            </Link>
          </p>
        </div>
        <Link to="/sessions">← Back to sessions</Link>
      </div>

      <div className="section">
        <div className="card">
          <dl className="kv">
            <dt>Duration</dt>
            <dd>{fmtDuration(session.started_at, session.ended_at)}</dd>
            <dt>Started</dt>
            <dd>{fmtTime(session.started_at)}</dd>
            <dt>Ended</dt>
            <dd>{fmtTime(session.ended_at)}</dd>
            {session.error && (
              <>
                <dt>Error</dt>
                <dd className="mono small">{session.error}</dd>
              </>
            )}
            {Object.keys(session.budget_state || {}).length > 0 && (
              <>
                <dt>Budget consumed</dt>
                <dd className="mono small">{JSON.stringify(session.budget_state)}</dd>
              </>
            )}
          </dl>
        </div>
      </div>

      {task && (
        <div className="section">
          <h2>Task result</h2>
          <div className="card">
            <dl className="kv">
              <dt>Status</dt>
              <dd>
                <Pill value={task.status} />
              </dd>
              <dt>Created by</dt>
              <dd className="mono small">{task.created_by ?? "—"}</dd>
              {task.error && (
                <>
                  <dt>Error</dt>
                  <dd className="mono small">{task.error}</dd>
                </>
              )}
            </dl>
            {task.result && (
              <pre style={{ marginTop: 10, fontSize: 12, overflowX: "auto" }}>
                {JSON.stringify(task.result, null, 2)}
              </pre>
            )}
          </div>
        </div>
      )}

      <div className="section">
        <h2>Governed actions in this session</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Action</th>
                <th>Resource</th>
                <th>Decision</th>
                <th>Rule</th>
                <th>Status</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {(!actions || actions.length === 0) && (
                <tr>
                  <td colSpan={6} className="empty-state">
                    No actions recorded for this session yet.
                  </td>
                </tr>
              )}
              {actions?.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.action_type}</td>
                  <td className="mono small">{a.resource ?? "—"}</td>
                  <td>
                    <Pill value={a.decision} />
                  </td>
                  <td className="mono small">{a.rule_id ?? "—"}</td>
                  <td>
                    <Pill value={a.status} />
                  </td>
                  <td className="small muted">{fmtTime(a.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
