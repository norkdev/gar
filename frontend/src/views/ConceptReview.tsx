// Gate 1: review (and optionally edit) the derived concept, then approve.
// After approval, the search phase runs server-side — we display live SSE
// activity while it runs.

import { useState } from "react";
import { approveConcept } from "../lib/api";
import type { RunState } from "../lib/api";
import { useRunStream } from "../lib/sse";
import { Activity } from "./Activity";

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
  const { events } = useRunStream(busy ? state.run_id : null);

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
      <h1>Gate 1 — Concept review</h1>
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

      <p style={{ marginTop: "1rem" }}>
        <button onClick={submit} disabled={busy}>
          {busy
            ? "Searching related work…"
            : draft === initial
              ? "Approve & search"
              : "Approve edits & search"}
        </button>
      </p>

      {busy && (
        <Activity
          events={events}
          hint="Live activity from the agent (LLM calls and public-source searches)."
        />
      )}
    </main>
  );
}
