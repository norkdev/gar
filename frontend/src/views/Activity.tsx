// Live activity feed during the long-running POSTs (search / compose phases).
// Subscribes to the SSE stream and renders the rolling audit log filtered to
// tools the user cares about.

import type { AuditEvent } from "../lib/sse";

export function Activity({ events, hint }: { events: AuditEvent[]; hint?: string }) {
  return (
    <div>
      {hint && <p className="muted">{hint}</p>}
      <div className="activity">
        {events.length === 0 && <div className="activity-line muted">Waiting for activity…</div>}
        {events.map((e, i) => (
          <div key={i} className="activity-line">
            <span className="muted">{e.timestamp.slice(11, 19)}</span>{" "}
            <strong>{e.tool_name}</strong> <span className="muted">{summarize(e)}</span>
          </div>
        ))}
      </div>
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
    return `“${q.slice(0, 60)}” → ${out.result_count ?? 0} results`;
  }
  if (e.tool_name === "grounding.validate") {
    return `is_valid=${out.is_valid} · unknown=${out.unknown_count}`;
  }
  return "";
}
