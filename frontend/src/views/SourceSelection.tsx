// Gate 2: review retrieved candidates and pick which to adopt for the
// final report. Submission triggers the compose-report phase.
//
// The pool can be large (hundreds of papers), which is a lot for a human to
// scan as one flat list. When the run computed semantic "directions" (the
// embedding-cluster slice), we group the candidates by direction — concept-
// nearest first, off-topic groups collapsed — so the human reviews by cluster
// the way an MCP client's LLM would organize subsets. Each group shows a page
// of candidates with "show more" (progressive disclosure). With no directions
// (BM25 mode) we fall back to a single relevance-ordered, paged list.

import { useMemo, useState } from "react";
import { Stepper } from "../components/Stepper";
import { candidateCompositeId, goBack, selectSources } from "../lib/api";
import type { Candidate, Direction, RunState } from "../lib/api";

const ABSTRACT_PREVIEW_CHARS = 280;
const GROUP_PAGE = 8; // candidates shown per group before "show more"
const RECOMMENDED_N = 6; // size of the one-click starter set
const OTHER_GROUP_ID = -1; // synthetic group for ungrouped / fallback candidates

type Group = {
  id: number;
  label: string;
  conceptNearest: boolean;
  candidates: Candidate[]; // in backend rerank (relevance) order
};

// Label a direction by its representative titles (deterministic — no LLM).
function directionLabel(d: Direction): string {
  const reps = d.representatives.filter(Boolean).slice(0, 2);
  if (reps.length === 0) return `Cluster ${d.id + 1}`;
  return reps.map((t) => (t.length > 60 ? t.slice(0, 60) + "…" : t)).join("  ·  ");
}

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
  const directions: Direction[] = useMemo(
    () => (state.context.directions as Direction[] | undefined) ?? [],
    [state.context],
  );

  const [adopted, setAdopted] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set()); // abstract expansion
  const [openOverride, setOpenOverride] = useState<Map<number, boolean>>(new Map());
  const [showAll, setShowAll] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const adoptedCount = adopted.size;

  // Group the pool by direction (concept-nearest first, then largest), with an
  // "Other" bucket for candidates dropped as cluster noise. No directions →
  // one relevance-ordered group.
  const groups: Group[] = useMemo(() => {
    if (candidates.length === 0) return [];
    if (directions.length === 0) {
      return [{ id: OTHER_GROUP_ID, label: "All candidates", conceptNearest: false, candidates }];
    }
    const known = new Set(directions.map((d) => d.id));
    const buckets = new Map<number, Candidate[]>();
    const other: Candidate[] = [];
    for (const c of candidates) {
      if (c.direction == null || !known.has(c.direction)) {
        other.push(c);
        continue;
      }
      let arr = buckets.get(c.direction);
      if (!arr) {
        arr = [];
        buckets.set(c.direction, arr);
      }
      arr.push(c);
    }
    const gs: Group[] = [];
    for (const d of directions) {
      const cs = buckets.get(d.id);
      if (cs && cs.length > 0) {
        gs.push({
          id: d.id,
          label: directionLabel(d),
          conceptNearest: d.contains_concept,
          candidates: cs,
        });
      }
    }
    gs.sort((a, b) =>
      a.conceptNearest !== b.conceptNearest
        ? a.conceptNearest
          ? -1
          : 1
        : b.candidates.length - a.candidates.length,
    );
    if (other.length > 0) {
      gs.push({ id: OTHER_GROUP_ID, label: "Other", conceptNearest: false, candidates: other });
    }
    return gs;
  }, [candidates, directions]);

  // A group is open if the user toggled it; otherwise the concept-nearest one
  // is open by default and the rest are collapsed.
  const isOpen = (g: Group) => openOverride.get(g.id) ?? g.conceptNearest;
  const toggleGroup = (g: Group) => setOpenOverride((m) => new Map(m).set(g.id, !isOpen(g)));

  const toggle = (id: string) =>
    setAdopted((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });

  const toggleExpanded = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });

  const adoptTop = (g: Group, n: number) =>
    setAdopted((prev) => {
      const next = new Set(prev);
      g.candidates.slice(0, n).forEach((c) => next.add(candidateCompositeId(c)));
      return next;
    });

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

  const backToConcept = async () => {
    if (
      !window.confirm(
        "Go back to edit the concept? Re-approving runs a new search, " +
          "replacing these candidates.",
      )
    )
      return;
    setBusy(true);
    setErr(null);
    try {
      const next = await goBack(state.run_id);
      onAdvanced(next); // back to the concept gate; App re-routes by status
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const grouped = directions.length > 0;

  return (
    <main>
      <Stepper status={state.status} />
      <h1>Adopt related work</h1>
      <p className="muted">
        {candidates.length} candidate{candidates.length === 1 ? "" : "s"} returned
        {grouped
          ? `, grouped into ${groups.length} relevance direction${groups.length === 1 ? "" : "s"}. The group nearest your idea is open; others are collapsed — expand any to review.`
          : ". Showing the most relevant first."}{" "}
        Check the ones to adopt; you can adopt zero — the report will say so honestly.
      </p>

      {groups.length === 0 && (
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
        {groups.map((g) => {
          const open = isOpen(g);
          const all = showAll.has(g.id);
          const visible = all ? g.candidates : g.candidates.slice(0, GROUP_PAGE);
          const hiddenCount = g.candidates.length - visible.length;
          return (
            <section
              key={g.id}
              style={{
                marginBottom: "var(--sp-3)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                overflow: "hidden",
              }}
            >
              <div
                className="row"
                style={{
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "var(--sp-2)",
                  padding: "var(--sp-2) var(--sp-3)",
                  background: "var(--color-surface-2)",
                }}
              >
                <button
                  type="button"
                  className="ghost"
                  onClick={() => toggleGroup(g)}
                  aria-expanded={open}
                  style={{
                    display: "flex",
                    gap: "var(--sp-2)",
                    alignItems: "baseline",
                    textAlign: "left",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span>
                  {g.conceptNearest && (
                    <span
                      style={{
                        fontSize: "var(--fs-xs)",
                        padding: "0.1em 0.5em",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--color-accent, #5b6cff)",
                        color: "#fff",
                        whiteSpace: "nowrap",
                      }}
                    >
                      nearest your idea
                    </span>
                  )}
                  <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {g.label}
                  </span>
                  <span className="muted" style={{ whiteSpace: "nowrap" }}>
                    {g.candidates.length}
                  </span>
                </button>
                {g.conceptNearest && (
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy}
                    onClick={() => adoptTop(g, RECOMMENDED_N)}
                    style={{ whiteSpace: "nowrap" }}
                  >
                    Adopt top {Math.min(RECOMMENDED_N, g.candidates.length)}
                  </button>
                )}
              </div>

              {open && (
                <div style={{ padding: "var(--sp-2) var(--sp-3) var(--sp-3)" }}>
                  {visible.map((c) => (
                    <CandidateRow
                      key={candidateCompositeId(c)}
                      c={c}
                      adopted={adopted.has(candidateCompositeId(c))}
                      expanded={expanded.has(candidateCompositeId(c))}
                      busy={busy}
                      onToggle={toggle}
                      onToggleExpand={toggleExpanded}
                    />
                  ))}
                  {hiddenCount > 0 && (
                    <button
                      type="button"
                      className="ghost"
                      style={{ marginTop: "var(--sp-2)" }}
                      onClick={() => setShowAll((s) => new Set(s).add(g.id))}
                    >
                      Show {hiddenCount} more in this group
                    </button>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {err && <div className="error">{err}</div>}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={submit} disabled={busy}>
          {busy ? "Starting compose…" : `Adopt ${adoptedCount} and compose report`}
        </button>
        <button type="button" className="secondary" onClick={backToConcept} disabled={busy}>
          ← Back to concept
        </button>
        {!busy && adoptedCount === 0 && (
          <span className="muted">
            Adopting zero produces an honest "no related work found" report.
          </span>
        )}
      </div>
    </main>
  );
}

function CandidateRow({
  c,
  adopted,
  expanded,
  busy,
  onToggle,
  onToggleExpand,
}: {
  c: Candidate;
  adopted: boolean;
  expanded: boolean;
  busy: boolean;
  onToggle: (id: string) => void;
  onToggleExpand: (id: string) => void;
}) {
  const id = candidateCompositeId(c);
  const previewNeeded = c.snippet.length > ABSTRACT_PREVIEW_CHARS;
  const displaySnippet =
    !previewNeeded || expanded ? c.snippet : c.snippet.slice(0, ABSTRACT_PREVIEW_CHARS) + "…";

  return (
    <div className={adopted ? "candidate adopted" : "candidate"}>
      <label
        style={{ display: "flex", alignItems: "flex-start", cursor: "pointer", gap: "var(--sp-1)" }}
      >
        <input
          type="checkbox"
          className="candidate-checkbox"
          checked={adopted}
          onChange={() => onToggle(id)}
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
              onClick={() => onToggleExpand(id)}
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
