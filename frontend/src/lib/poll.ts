// Poll a run's status while a segment runs server-side.
//
// The backend runs each segment (derive / search / compose) off the request
// thread, so POSTs return an in-progress snapshot immediately. This hook polls
// GET /runs/{id} until the run settles on a gate (awaiting_*) or a terminal
// status, then calls onSettled once. Replaces the old SSE feed, which tailed a
// local audit file and can't work behind the Lambda Function URL.

import { useEffect, useRef, useState } from "react";
import { getActivity, getRun, isInProgress, type ActivityItem, type RunState } from "./api";

const POLL_INTERVAL_MS = 2500;

export function useRunProgress(
  state: RunState,
  onSettled: (next: RunState) => void,
): { phase: RunState; error: string | null } {
  const [phase, setPhase] = useState<RunState>(state);
  const [error, setError] = useState<string | null>(null);
  // onSettled may be a fresh closure each render; read it through a ref so the
  // polling effect can depend on run_id alone (one loop per run).
  const settledCb = useRef(onSettled);
  useEffect(() => {
    settledCb.current = onSettled;
  });

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const next = await getRun(state.run_id, controller.signal);
        if (controller.signal.aborted) return;
        setPhase(next);
        if (isInProgress(next.status)) {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        } else {
          settledCb.current(next);
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    };

    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [state.run_id]);

  return { phase, error };
}

// Accumulate the run's activity lines while a segment runs. Polls
// GET /runs/{id}/activity, sending the count it already has so each poll
// transfers only new lines; the server returns the running `total`. Errors are
// swallowed — the feed is a progress nicety, not load-bearing, so a hiccup must
// never break the Processing view (useRunProgress owns the real error path).
export function useActivity(runId: string): ActivityItem[] {
  const [items, setItems] = useState<ActivityItem[]>([]);
  // Read the current length inside the poll loop without making it an effect
  // dep; mirror it through a ref updated after render (same pattern as the
  // settledCb above).
  const countRef = useRef(0);
  useEffect(() => {
    countRef.current = items.length;
  });

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    // Reset when the run changes so a new run doesn't inherit stale lines. The
    // effect runs only on runId change (its sole dep), so this can't loop.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setItems([]);
    countRef.current = 0;

    const tick = async () => {
      try {
        const { items: fresh } = await getActivity(runId, countRef.current);
        if (controller.signal.aborted) return;
        if (fresh.length) setItems((prev) => [...prev, ...fresh]);
      } catch {
        // ignore — try again next tick
      }
      if (!controller.signal.aborted) timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId]);

  return items;
}
