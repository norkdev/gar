// Phase progress indicator. Shown at the top of each gated view so the
// reviewer always knows where they are in the run.

import type { RunStatus } from "../lib/api";

const PHASES = ["Concept", "Sources", "Report"] as const;
type Phase = (typeof PHASES)[number];

function phaseFromStatus(status: RunStatus): { current: Phase | null; done: number } {
  switch (status) {
    case "deriving_concept":
      return { current: "Concept", done: 0 };
    case "awaiting_concept_approval":
      return { current: "Concept", done: 0 };
    case "searching":
      return { current: "Sources", done: 1 };
    case "awaiting_source_selection":
      return { current: "Sources", done: 1 };
    case "evaluating":
      return { current: "Report", done: 2 };
    case "awaiting_report_approval":
      return { current: "Report", done: 2 };
    case "completed":
      return { current: null, done: 3 };
    case "failed":
      return { current: null, done: 0 };
  }
}

export function Stepper({ status }: { status: RunStatus }) {
  const { current, done } = phaseFromStatus(status);

  return (
    <nav className="stepper" aria-label="Run progress">
      {PHASES.map((phase, idx) => {
        const isDone = idx < done;
        const isCurrent = phase === current;
        const cls = ["stepper-step", isDone && "done", isCurrent && "current"]
          .filter(Boolean)
          .join(" ");
        return (
          <span key={phase} style={{ display: "contents" }}>
            <span className={cls}>
              <span className="stepper-dot" aria-hidden="true">
                {isDone ? "✓" : idx + 1}
              </span>
              <span className="stepper-label">{phase}</span>
            </span>
            {idx < PHASES.length - 1 && <span className="stepper-bar" aria-hidden="true" />}
          </span>
        );
      })}
    </nav>
  );
}
