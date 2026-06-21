// "My sessions" — the user's runs (D-204). Lists each session; open it to
// resume at its gate or view its report, download a finished report, or delete
// (which purges the record + its S3 objects, right-to-be-forgotten).

import { useEffect, useState } from "react";
import { deleteRun, getRun, getRunReport, listRuns } from "../lib/api";
import type { RunState, RunStatus, SessionSummary } from "../lib/api";
import { downloadText, reportFilename } from "../lib/download";

const STATUS_LABEL: Record<RunStatus, string> = {
  deriving_concept: "Deriving concept",
  awaiting_concept_approval: "Concept review",
  searching: "Searching",
  awaiting_source_selection: "Source selection",
  evaluating: "Composing report",
  awaiting_report_approval: "Report review",
  completed: "Completed",
  failed: "Failed",
};

export function SessionList({
  onOpen,
  onNew,
}: {
  onOpen: (s: RunState) => void;
  onNew: () => void;
}) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    listRuns()
      .then(setSessions)
      .catch((e) => {
        setErr(e instanceof Error ? e.message : String(e));
        setSessions([]);
      });
  }, []);

  const open = async (id: string) => {
    setBusyId(id);
    setErr(null);
    try {
      onOpen(await getRun(id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const download = async (id: string) => {
    setBusyId(id);
    setErr(null);
    try {
      const { report } = await getRunReport(id);
      downloadText(report, reportFilename());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Delete this session? Its report and audit trail are permanently purged."))
      return;
    setBusyId(id);
    setErr(null);
    try {
      await deleteRun(id);
      setSessions((prev) => (prev ?? []).filter((s) => s.run_id !== id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main>
      <div className="row-between">
        <h1>My sessions</h1>
        <button onClick={onNew}>New survey</button>
      </div>

      {err && <div className="error">{err}</div>}

      {sessions === null ? (
        <p className="muted">
          <span className="spinner" aria-hidden="true" /> Loading…
        </p>
      ) : sessions.length === 0 ? (
        <p className="muted">No sessions yet. Start a survey to create one.</p>
      ) : (
        <ul className="session-list">
          {sessions.map((s) => {
            const busy = busyId === s.run_id;
            return (
              <li key={s.run_id} className="session-row">
                <div className="session-main">
                  <div className="session-concept">
                    {s.concept ?? <span className="subtle">(deriving concept…)</span>}
                  </div>
                  <div className="subtle">
                    {STATUS_LABEL[s.status]} · {new Date(s.updated_at).toLocaleString()}
                  </div>
                </div>
                <div className="row">
                  <button onClick={() => open(s.run_id)} disabled={busy}>
                    Open
                  </button>
                  {s.has_report && (
                    <button className="ghost" onClick={() => download(s.run_id)} disabled={busy}>
                      Download
                    </button>
                  )}
                  <button className="ghost" onClick={() => remove(s.run_id)} disabled={busy}>
                    Delete
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
