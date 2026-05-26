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
      <h1>Guided Agentic Retrieval</h1>
      <p className="muted">
        Survey published literature against your private idea notes. The agent gathers grounded
        candidates and stops there — the novelty judgement stays with you.
      </p>

      <h2>Idea source</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Point this at a folder of Markdown notes (an Obsidian vault or a sub-folder), or a single{" "}
        <code>.md</code> file. v1 reads Markdown only.
      </p>
      <input
        type="text"
        value={vaultPath}
        onChange={(e) => setVaultPath(e.target.value)}
        placeholder="/path/to/vault or /path/to/note.md"
        spellCheck={false}
      />
      <p className="subtle" style={{ marginTop: "var(--sp-2)" }}>
        Reports save into the same folder (or the parent if a file is given) and the filename is
        appended to <code>.ignore</code> so re-runs skip it.
      </p>

      {err && <div className="error">{err}</div>}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={submit} disabled={busy || vaultPath.trim() === ""}>
          {busy ? "Deriving concept…" : "Start"}
        </button>
        {busy && <span className="muted">The first LLM call usually takes 5–15 seconds.</span>}
      </div>
    </main>
  );
}
