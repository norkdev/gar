// Poll a run's status while a segment runs server-side.
//
// The backend runs each segment (derive / search / compose) off the request
// thread, so POSTs return an in-progress snapshot immediately. This hook polls
// GET /runs/{id} until the run settles on a gate (awaiting_*) or a terminal
// status, then calls onSettled once. Replaces the old SSE feed, which tailed a
// local audit file and can't work behind the Lambda Function URL.

import { useEffect, useRef, useState } from "react";
import { getRun, isInProgress, type RunState } from "./api";

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
