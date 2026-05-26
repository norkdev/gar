// Entry view: enter a vault path and start a run.

import { useState } from "react";
import { createRun } from "../lib/api";
import type { RunState } from "../lib/api";

export function Start({ onStarted }: { onStarted: (s: RunState) => void }) {
  const [vaultPath, setVaultPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const state = await createRun(vaultPath.trim());
      onStarted(state);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main>
      <h1>Guided Agentic Retrieval — Literature Survey</h1>
      <p className="muted">
        Point this at a folder of Markdown idea notes (an Obsidian vault or a sub-folder), or a
        single .md file. The agent will derive a concept and survey public literature for related
        work.
      </p>

      <h2>Idea source</h2>
      <input
        type="text"
        value={vaultPath}
        onChange={(e) => setVaultPath(e.target.value)}
        placeholder="/path/to/vault or /path/to/note.md"
        spellCheck={false}
      />
      <p className="muted" style={{ marginTop: "0.5rem" }}>
        v1 supports Markdown only. Reports save into the same folder (the parent if a file is
        given), so the agent must have read+write access.
      </p>

      {err && <div className="error">{err}</div>}

      <p style={{ marginTop: "1.5rem" }}>
        <button onClick={submit} disabled={busy || vaultPath.trim() === ""}>
          {busy ? "Deriving concept…" : "Start"}
        </button>
        {busy && (
          <span className="muted" style={{ marginLeft: "1rem" }}>
            The first LLM call usually takes 5–15 seconds.
          </span>
        )}
      </p>
    </main>
  );
}
