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

import { useEffect, useRef, useState } from "react";
import type { AuditEvent } from "../lib/sse";

// How close to the bottom (px) counts as "near the bottom" for auto-tailing.
// ~2-3 mono lines of slack so a small manual nudge still keeps tailing.
const STICK_THRESHOLD_PX = 48;

export function Activity({ events, hint }: { events: AuditEvent[]; hint?: string }) {
  const [open, setOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  // Whether to tail the newest line. Starts true (pinned to bottom); flips as
  // the user scrolls. We only follow new events while they're near the bottom,
  // so scrolling up to read older lines isn't yanked back down by the next
  // event. Re-pins when they scroll back to the bottom.
  const stick = useRef(true);

  function onScroll() {
    const el = logRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stick.current = distanceFromBottom < STICK_THRESHOLD_PX;
  }

  // On open, jump to the newest line; on each new event, tail it only if the
  // user is currently near the bottom.
  useEffect(() => {
    const el = logRef.current;
    if (el && open && stick.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [open, events.length]);
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
          onClick={() =>
            setOpen((v) => {
              // Opening (or reopening) should land on the newest line.
              if (!v) stick.current = true;
              return !v;
            })
          }
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
        <div
          id="activity-log"
          ref={logRef}
          onScroll={onScroll}
          className="activity"
          role="log"
          aria-live="polite"
        >
          {events.length === 0 && (
            <div className="activity-line">
              <span className="activity-summary">Waiting for activity…</span>
            </div>
          )}
          {events.map((e, i) => (
            <div key={i} className={e.status === "error" ? "activity-line error" : "activity-line"}>
              <span className="activity-time">{localTime(e.timestamp)}</span>
              <span className="activity-tool">{e.tool_name}</span>
              <span className="activity-summary">{summarize(e)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Audit timestamps are ISO 8601 in UTC; render HH:MM:SS in the viewer's own
// timezone so the feed reads as local wall-clock time, not UTC. Falls back to
// a raw slice if the value isn't a parseable date.
function localTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 19);
  return d.toLocaleTimeString([], { hour12: false });
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
