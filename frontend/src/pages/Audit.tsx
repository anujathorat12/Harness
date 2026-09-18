import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useApiData } from "../hooks";
import { Empty, ErrorBanner, Loading, Pill, fmtTime } from "../components";

const FILTER_FIELDS = ["agent_id", "session_id", "task_id", "action_id", "event_type"] as const;

export default function Audit() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const { data, loading, error } = useApiData(() => api.queryAudit(filters), [filters]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const cleaned: Record<string, string> = {};
    for (const [k, v] of Object.entries(draft)) if (v.trim()) cleaned[k] = v.trim();
    setFilters(cleaned);
  }

  function clear() {
    setDraft({});
    setFilters({});
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Audit</h1>
          <p>
            Append-only trail — nothing here can be edited or deleted through any API. Reconstruct any action's
            full story: agent → session → action → policy → rule → decision → approval → execution outcome.
          </p>
        </div>
      </div>

      <form className="card section" onSubmit={onSubmit}>
        <div className="grid cols-4">
          {FILTER_FIELDS.map((field) => (
            <div className="field" key={field} style={{ marginBottom: 0 }}>
              <label>{field}</label>
              <input
                className="mono"
                value={draft[field] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, [field]: e.target.value }))}
              />
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button type="submit" className="primary">
            Filter
          </button>
          <button type="button" onClick={clear}>
            Clear
          </button>
        </div>
      </form>

      <ErrorBanner message={error} />
      {loading && <Loading />}
      {data && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Actor</th>
                <th>Decision</th>
                <th>Policy / Rule</th>
                <th>Agent</th>
                <th>Session</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <Empty>No matching audit events.</Empty>
                  </td>
                </tr>
              )}
              {data.map((e) => (
                <tr key={e.id}>
                  <td className="small muted">{fmtTime(e.timestamp)}</td>
                  <td className="mono small">{e.event_type}</td>
                  <td className="mono small">{e.actor}</td>
                  <td>
                    <Pill value={e.decision} />
                  </td>
                  <td className="mono small">{e.rule_id ?? "—"}</td>
                  <td className="mono small">{e.agent_id?.slice(0, 8) ?? "—"}</td>
                  <td className="mono small">{e.session_id?.slice(0, 8) ?? "—"}</td>
                  <td className="mono small">{e.action_id?.slice(0, 8) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
