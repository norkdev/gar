// Gate 1: review (and optionally edit) the derived concept, then approve.
// After approval the search phase runs server-side; App switches to the
// Processing view, which polls until the next gate.

import { useState } from "react";
import { Stepper } from "../components/Stepper";
import { approveConcept } from "../lib/api";
import type { RunState } from "../lib/api";

export function ConceptReview({
  state,
  onAdvanced,
}: {
  state: RunState;
  onAdvanced: (next: RunState) => void;
}) {
  const initial = state.pending_payload.concept ?? "";
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const edited = draft !== initial ? draft : undefined;
      const next = await approveConcept(state.run_id, edited);
      onAdvanced(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main>
      <Stepper status={state.status} />
      <h1>Concept review</h1>
      <p className="muted">
        The agent derived this concept from your notes. Edit it if needed. Approving triggers the
        related-work search (typically 1–3 minutes).
      </p>

      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={busy}
        rows={14}
      />

      {err && <div className="error">{err}</div>}

      <div className="row" style={{ marginTop: "var(--sp-4)" }}>
        <button onClick={submit} disabled={busy}>
          {busy
            ? "Starting search…"
            : draft === initial
              ? "Approve & search"
              : "Approve edits & search"}
        </button>
        {draft !== initial && !busy && (
          <button className="ghost" onClick={() => setDraft(initial)}>
            Revert edits
          </button>
        )}
      </div>
    </main>
  );
}
