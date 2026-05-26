// Terminal screen after gate 3 approval (or after a FAILED run).

import { Stepper } from "../components/Stepper";
import type { RunState } from "../lib/api";

export function Completed({ state, onRestart }: { state: RunState; onRestart: () => void }) {
  const failed = state.status === "failed";
  return (
    <main>
      <Stepper status={state.status} />
      <h1>{failed ? "Run failed" : "Done"}</h1>

      {failed ? (
        <div className="error">{state.error ?? "Unknown failure."}</div>
      ) : (
        <>
          <p>Report saved at:</p>
          <div className="saved-path">{state.saved_path ?? "(path not returned)"}</div>
          <p className="muted">
            The filename has been appended to <code>.ignore</code> in the same folder, so future
            runs will skip it.
          </p>
        </>
      )}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={onRestart} className="secondary">
          Start a new run
        </button>
      </div>
    </main>
  );
}
