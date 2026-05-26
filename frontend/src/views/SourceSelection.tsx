// Gate 2: review retrieved candidates and pick which to adopt for the
// final report. Submission triggers the compose-report phase.

import { useMemo, useState } from "react";
import { Stepper } from "../components/Stepper";
import { candidateCompositeId, selectSources } from "../lib/api";
import type { Candidate, RunState } from "../lib/api";
import { useRunStream } from "../lib/sse";
import { Activity } from "./Activity";

const ABSTRACT_PREVIEW_CHARS = 280;

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
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
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

  const toggleExpanded = (id: string) => {
    setExpanded((prev) => {
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
      <Stepper status={state.status} />
      <h1>Adopt related work</h1>
      <p className="muted">
        {candidates.length} candidate{candidates.length === 1 ? "" : "s"} returned. Check the ones
        to adopt into the final report. You can adopt zero — the report will say so honestly.
      </p>

      {sorted.length === 0 && (
        <div
          className="muted"
          style={{
            padding: "var(--sp-5)",
            border: "1px dashed var(--color-border)",
            borderRadius: "var(--radius-md)",
            textAlign: "center",
            fontStyle: "italic",
          }}
        >
          No candidates were returned. The agent may have decided it had enough context, or upstream
          sources rate-limited.
        </div>
      )}

      <div style={{ marginTop: "var(--sp-4)" }}>
        {sorted.map((c) => {
          const id = candidateCompositeId(c);
          const isAdopted = adopted.has(id);
          const isExpanded = expanded.has(id);
          const previewNeeded = c.snippet.length > ABSTRACT_PREVIEW_CHARS;
          const displaySnippet =
            !previewNeeded || isExpanded
              ? c.snippet
              : c.snippet.slice(0, ABSTRACT_PREVIEW_CHARS) + "…";

          return (
            <div key={id} className={isAdopted ? "candidate adopted" : "candidate"}>
              <label
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  cursor: "pointer",
                  gap: "var(--sp-1)",
                }}
              >
                <input
                  type="checkbox"
                  className="candidate-checkbox"
                  checked={isAdopted}
                  onChange={() => toggle(id)}
                  disabled={busy}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="candidate-title">{c.title || "(no title)"}</div>
                  <div className="candidate-meta">
                    <span>
                      {c.source_name}:{c.external_id}
                    </span>
                    {c.authors.length > 0 && (
                      <>
                        <span className="candidate-meta-sep">·</span>
                        <span>
                          {c.authors.slice(0, 3).join(", ")}
                          {c.authors.length > 3 && " et al."}
                        </span>
                      </>
                    )}
                    {c.published && (
                      <>
                        <span className="candidate-meta-sep">·</span>
                        <span>{c.published.slice(0, 10)}</span>
                      </>
                    )}
                    {c.url && (
                      <>
                        <span className="candidate-meta-sep">·</span>
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          open ↗
                        </a>
                      </>
                    )}
                  </div>
                </div>
              </label>

              {c.snippet && (
                <div className="candidate-abstract">
                  {displaySnippet}
                  {previewNeeded && (
                    <button
                      type="button"
                      className="ghost"
                      style={{ marginLeft: "var(--sp-2)" }}
                      onClick={() => toggleExpanded(id)}
                    >
                      {isExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {err && <div className="error">{err}</div>}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={submit} disabled={busy}>
          {busy ? "Composing report…" : `Adopt ${adoptedCount} and compose report`}
        </button>
        {!busy && adoptedCount === 0 && (
          <span className="muted">
            Adopting zero produces an honest "no related work found" report.
          </span>
        )}
      </div>

      {busy && (
        <Activity
          events={events}
          hint="Composing the final report (1 LLM call, possibly 1 grounding-retry)."
        />
      )}
    </main>
  );
}
