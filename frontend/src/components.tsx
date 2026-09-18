// Small shared UI pieces used across pages. No business logic lives here --
// just rendering helpers.
import type { ReactNode } from "react";

export function Pill({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="pill neutral">—</span>;
  return <span className={`pill ${value}`}>{value.replace(/_/g, " ")}</span>;
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="error-banner">{message}</div>;
}

export function Loading() {
  return <div className="empty-state">Loading…</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString();
}

export function fmtDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso) return "—";
  const start = new Date(startIso.endsWith("Z") ? startIso : startIso + "Z").getTime();
  const end = endIso ? new Date(endIso.endsWith("Z") ? endIso : endIso + "Z").getTime() : Date.now();
  const seconds = Math.max(0, (end - start) / 1000);
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.slice(0, 8);
}
