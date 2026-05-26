// Live activity feed during long-running POSTs (search / compose phases).
//
// Layout:
//   ┌─────────────────────────────────┐
//   │  ███████░░░░░░░░░░░░░░░░░░░░░░  │  indeterminate progress bar
//   ├─────────────────────────────────┤
//   │  ▸ Activity  (12 events)        │  collapsible header
//   ├─────────────────────────────────┤
//   │   …event lines…                 │  visible only when expanded
//   └─────────────────────────────────┘
//
// Default collapsed, since the event lines pile up quickly and push the
// approval buttons off-screen. The progress bar stays visible regardless
// so the user can see *something* is happening.

import { useState } from "react";
import type { AuditEvent } from "../lib/sse";

export function Activity({ events, hint }: { events: AuditEvent[]; hint?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="activity-wrapper">
      {hint && (
        <p className="subtle" style={{ marginTop: 0, marginBottom: "var(--sp-2)" }}>
          {hint}
        </p>
      )}
      <div className="activity-progress" role="progressbar" aria-label="Agent working" />
      <div className="activity-header">
        <button
          type="button"
          className={open ? "activity-toggle open" : "activity-toggle"}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="activity-log"
        >
          <span className="activity-caret" aria-hidden="true">
            ▶
          </span>
          <span>Activity</span>
        </button>
        <span className="activity-count">
          {events.length} event{events.length === 1 ? "" : "s"}
        </span>
      </div>
      {open && (
        <div id="activity-log" className="activity" role="log" aria-live="polite">
          {events.length === 0 && (
            <div className="activity-line">
              <span className="activity-summary">Waiting for activity…</span>
            </div>
          )}
          {events.map((e, i) => (
            <div key={i} className={e.status === "error" ? "activity-line error" : "activity-line"}>
              <span className="activity-time">{e.timestamp.slice(11, 19)}</span>
              <span className="activity-tool">{e.tool_name}</span>
              <span className="activity-summary">{summarize(e)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function summarize(e: AuditEvent): string {
  if (e.status === "error") {
    return `ERROR: ${(e.error || "").slice(0, 80)}`;
  }
  const out = e.output || {};
  if (e.tool_name === "llm.complete") {
    return `${out.text_blocks ?? 0} text · ${out.tool_uses ?? 0} tool_use · ${out.stop_reason ?? "?"}`;
  }
  if (e.tool_name.startsWith("search_")) {
    const q = (e.input.query as string) || "";
    return `"${q.slice(0, 60)}" → ${out.result_count ?? 0} results`;
  }
  if (e.tool_name === "grounding.validate") {
    return `is_valid=${out.is_valid} · unknown=${out.unknown_count}`;
  }
  return "";
}
