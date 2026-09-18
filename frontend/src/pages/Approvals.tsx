import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApiData } from "../hooks";
import { Empty, ErrorBanner, Loading, fmtTime } from "../components";
import type { Action, Approval } from "../types";

function ResolveButtons({ approvalId, onResolved }: { approvalId: string; onResolved: () => void }) {
  const [busy, setBusy] = useState<"allow" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function resolve(approve: boolean) {
    setBusy(approve ? "allow" : "deny");
    setError(null);
    try {
      await api.resolveApproval(approvalId, approve);
      onResolved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 6 }}>
        <button className="allow" disabled={busy !== null} onClick={() => resolve(true)}>
          {busy === "allow" ? "Allowing…" : "Allow"}
        </button>
        <button className="deny" disabled={busy !== null} onClick={() => resolve(false)}>
          {busy === "deny" ? "Denying…" : "Deny"}
        </button>
      </div>
      {error && (
        <div className="small" style={{ color: "var(--deny)", marginTop: 4 }}>
          {error}
        </div>
      )}
    </div>
  );
}

function ApprovalRow({ approval, onResolved }: { approval: Approval; onResolved: () => void }) {
  const [action, setAction] = useState<Action | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAction(approval.action_id)
      .then((a) => {
        if (!cancelled) setAction(a);
      })
      .catch(() => {
        // best-effort enrichment only -- the row still works without it
      });
    return () => {
      cancelled = true;
    };
  }, [approval.action_id]);

  return (
    <tr>
      <td className="mono small">{action?.action_type ?? "…"}</td>
      <td className="mono small">{action?.resource ?? "—"}</td>
      <td>
        {action ? (
          <Link to={`/agents/${action.agent_id}`} className="mono small">
            {action.agent_id.slice(0, 8)}
          </Link>
        ) : (
          "…"
        )}
      </td>
      <td className="small muted">{fmtTime(approval.created_at)}</td>
      <td className="small muted">{fmtTime(approval.timeout_at)}</td>
      <td>
        <ResolveButtons approvalId={approval.id} onResolved={onResolved} />
      </td>
    </tr>
  );
}

export default function Approvals() {
  const { data, loading, error, reload } = useApiData(() => api.listApprovals("pending"));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Approvals</h1>
          <p>Human-in-the-loop: nothing here executes until ALLOW or DENY is clicked. Resolution is final.</p>
        </div>
        <button onClick={reload}>Refresh</button>
      </div>
      <ErrorBanner message={error} />
      {loading && <Loading />}
      {data && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Action type</th>
                <th>Resource</th>
                <th>Agent</th>
                <th>Requested</th>
                <th>Timeout</th>
                <th>Decide</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <Empty>No approvals waiting — nothing is paused right now.</Empty>
                  </td>
                </tr>
              )}
              {data.map((a) => (
                <ApprovalRow key={a.id} approval={a} onResolved={reload} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted" style={{ marginTop: 14 }}>
        For the full decision chain (policy, rule, version), open the Audit page and filter by this action's ID.
      </p>
    </div>
  );
}
