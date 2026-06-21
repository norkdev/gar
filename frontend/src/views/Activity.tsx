// Live activity feed during the long-running phases (search / compose).
//
// Layout:
//   ┌─────────────────────────────────┐
//   │  ███████░░░░░░░░░░░░░░░░░░░░░░  │  indeterminate progress bar
//   ├─────────────────────────────────┤
//   │  ▸ Activity  (12 steps)         │  collapsible header
//   ├─────────────────────────────────┤
//   │   …step lines…                  │  visible only when expanded
//   └─────────────────────────────────┘
//
// Default collapsed (just a count + progress bar) so the lines don't push the
// page around; expand to watch what the agent is doing. Fed by polled
// ActivityItems (lib/poll → useActivity), each already a human-readable line —
// this replaces the retired SSE feed, which can't work behind the Function URL.

import { useEffect, useRef, useState } from "react";
import type { ActivityItem } from "../lib/api";

// How close to the bottom (px) still counts as "near the bottom" for auto-tailing.
const STICK_THRESHOLD_PX = 48;

export function Activity({ items, hint }: { items: ActivityItem[]; hint?: string }) {
  const [open, setOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  // Tail the newest line only while the user is near the bottom, so scrolling
  // up to read older lines isn't yanked back down by the next poll.
  const stick = useRef(true);

  function onScroll() {
    const el = logRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
  }

  useEffect(() => {
    const el = logRef.current;
    if (el && open && stick.current) el.scrollTop = el.scrollHeight;
  }, [open, items.length]);

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
              if (!v) stick.current = true; // opening lands on the newest line
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
          {items.length} step{items.length === 1 ? "" : "s"}
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
          {items.length === 0 ? (
            <div className="activity-line">
              <span className="activity-summary">Waiting for the first step…</span>
            </div>
          ) : (
            items.map((it, i) => (
              <div
                key={i}
                className={it.status === "error" ? "activity-line error" : "activity-line"}
              >
                <span className="activity-time">{localTime(it.timestamp)}</span>
                <span className="activity-summary">{it.text}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// Audit timestamps are ISO 8601 UTC; render HH:MM:SS in the viewer's timezone.
function localTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 19);
  return d.toLocaleTimeString([], { hour12: false });
}
