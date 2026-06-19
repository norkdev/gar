// Shown while a segment runs server-side (deriving / searching / composing).
// Polls GET /runs/{id} until the run reaches the next gate or a terminal
// status, then hands the settled state back up to App.

import { Stepper } from "../components/Stepper";
import type { RunState, RunStatus } from "../lib/api";
import { useRunProgress } from "../lib/poll";

const PHASE_LABEL: Partial<Record<RunStatus, string>> = {
  deriving_concept: "Deriving the core concept from your notes…",
  searching: "Searching the literature and ranking related work…",
  evaluating: "Composing the grounded, cited report…",
};

export function Processing({
  state,
  onAdvanced,
  onRestart,
}: {
  state: RunState;
  onAdvanced: (next: RunState) => void;
  onRestart: () => void;
}) {
  const { phase, error } = useRunProgress(state, onAdvanced);

  if (error) {
    return (
      <main>
        <h1>Lost contact with the run</h1>
        <p className="error">{error}</p>
        <p className="muted">
          The run is durable server-side. Reload to resume polling, or start over.
        </p>
        <p style={{ marginTop: "var(--sp-4)" }}>
          <button className="secondary" onClick={onRestart}>
            Start a new run
          </button>
        </p>
      </main>
    );
  }

  return (
    <main>
      <Stepper status={phase.status} />
      <h1>Working…</h1>
      <p className="muted" aria-live="polite">
        <span className="spinner" aria-hidden="true" /> {PHASE_LABEL[phase.status] ?? phase.status}
      </p>
      <p className="muted" style={{ marginTop: "var(--sp-3)" }}>
        This typically takes 1–3 minutes. You can leave this page open; the run continues on the
        server.
      </p>
    </main>
  );
}
