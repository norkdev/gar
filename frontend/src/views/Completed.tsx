// Terminal screen after gate 3 approval (or after a FAILED run).

import type { RunState } from "../lib/api";

export function Completed({ state, onRestart }: { state: RunState; onRestart: () => void }) {
  const failed = state.status === "failed";
  return (
    <main>
      <h1>{failed ? "Run failed" : "Done"}</h1>

      {failed ? (
        <div className="error">{state.error ?? "Unknown failure."}</div>
      ) : (
        <>
          <p>Report saved at:</p>
          <pre className="report">{state.saved_path ?? "(path not returned)"}</pre>
          <p className="muted">
            The filename has been appended to <code>.ignore</code> in the same folder, so future
            runs will skip it.
          </p>
        </>
      )}

      <p style={{ marginTop: "1.5rem" }}>
        <button onClick={onRestart} className="secondary">
          Start a new run
        </button>
      </p>
    </main>
  );
}
