// Gate 2: review retrieved candidates and pick which to adopt for the
// final report. Submission triggers the compose-report phase.

import { useMemo, useState } from "react";
import { candidateCompositeId, selectSources } from "../lib/api";
import type { Candidate, RunState } from "../lib/api";
import { useRunStream } from "../lib/sse";
import { Activity } from "./Activity";

export function SourceSelection({
  state,
  onAdvanced,
}: {
  state: RunState;
  onAdvanced: (next: RunState) => void;
}) {
  const candidates: Candidate[] = useMemo(
    () => state.pending_payload.candidates ?? [],
    [state.pending_payload.candidates],
  );
  const [adopted, setAdopted] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { events } = useRunStream(busy ? state.run_id : null);

  const adoptedCount = adopted.size;

  const toggle = (id: string) => {
    setAdopted((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const next = await selectSources(state.run_id, Array.from(adopted));
      onAdvanced(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Newest publication date first. ISO 8601 timestamps sort
  // lexicographically, so a direct string compare gives the right order.
  // Candidates without a publication date go to the end.
  const sorted = useMemo(
    () =>
      [...candidates].sort((a, b) => {
        if (!a.published && !b.published) return 0;
        if (!a.published) return 1;
        if (!b.published) return -1;
        return b.published.localeCompare(a.published);
      }),
    [candidates],
  );

  return (
    <main>
      <h1>Gate 2 — Adopt related work</h1>
      <p className="muted">
        {candidates.length} candidate{candidates.length === 1 ? "" : "s"} found. Check the ones to
        adopt into the final report. You can adopt zero — the report will say so honestly.
      </p>

      {sorted.length === 0 && (
        <p className="muted" style={{ fontStyle: "italic" }}>
          No candidates were returned. (This can happen if the agent decided it had enough context,
          or if upstream sources rate-limited.)
        </p>
      )}

      {sorted.map((c) => {
        const id = candidateCompositeId(c);
        const isAdopted = adopted.has(id);
        return (
          <div key={id} className={isAdopted ? "candidate adopted" : "candidate"}>
            <label style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={isAdopted}
                onChange={() => toggle(id)}
                disabled={busy}
                style={{ width: "auto", marginRight: "0.5rem" }}
              />
              <strong>{c.title || "(no title)"}</strong>
            </label>
            <div className="muted" style={{ marginTop: "0.25rem" }}>
              [{c.source_name}:{c.external_id}]
              {c.authors.length > 0 && " · " + c.authors.slice(0, 3).join(", ")}
              {c.authors.length > 3 && " et al."}
              {c.published && " · " + c.published.slice(0, 10)}
              {c.url && (
                <>
                  {" · "}
                  <a href={c.url} target="_blank" rel="noopener noreferrer">
                    open
                  </a>
                </>
              )}
            </div>
            {c.snippet && (
              <div style={{ marginTop: "0.4rem", fontSize: "0.88rem" }}>
                {c.snippet.length > 320 ? c.snippet.slice(0, 320) + "…" : c.snippet}
              </div>
            )}
          </div>
        );
      })}

      {err && <div className="error">{err}</div>}

      <p style={{ marginTop: "1rem" }}>
        <button onClick={submit} disabled={busy}>
          {busy ? "Composing report…" : `Adopt ${adoptedCount} and compose report`}
        </button>
      </p>

      {busy && (
        <Activity
          events={events}
          hint="Composing the final report (1 LLM call, possibly 1 grounding-retry)."
        />
      )}
    </main>
  );
}
