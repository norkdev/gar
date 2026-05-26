// Gate 3: review the composed report and approve to finish the run.
//
// "Approve & save" opens a native file-save dialog (showSaveFilePicker
// where supported; standard <a download> fallback on Safari/Firefox)
// before the gate-transition POST. Cancelling the dialog still
// completes the run — the save is the user's choice, the approval is
// the governance step. We save first because once approved the user
// is navigated away from this screen; the report is otherwise lost.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { Stepper } from "../components/Stepper";
import { approveReport } from "../lib/api";
import type { RunState } from "../lib/api";

type Tab = "rendered" | "raw";

// Native save-file picker — Chrome / Edge as of 2026. Safari and Firefox
// fall back to a regular download. Typed locally to avoid pulling in a
// global types package for one API.
interface SaveFilePickerType {
  description?: string;
  accept: Record<string, string[]>;
}
interface FileSystemWritableStream {
  write: (data: BlobPart) => Promise<void>;
  close: () => Promise<void>;
}
interface SaveFilePickerHandle {
  createWritable: () => Promise<FileSystemWritableStream>;
}
interface WindowWithSaveFilePicker {
  showSaveFilePicker?: (opts: {
    suggestedName?: string;
    types?: SaveFilePickerType[];
  }) => Promise<SaveFilePickerHandle>;
}

function suggestedReportFilename(): string {
  const today = new Date().toISOString().slice(0, 10);
  return `gar-report-${today}.md`;
}

function isUserCancellation(e: unknown): boolean {
  return e instanceof Error && e.name === "AbortError";
}

/** Try the native save-file picker; fall back to a standard download.
 *  Returns true if a save was actually performed (or attempted by the
 *  fallback). Returns false only when the user cancelled the dialog. */
async function saveReportToFile(content: string): Promise<boolean> {
  const suggestedName = suggestedReportFilename();
  const win = window as WindowWithSaveFilePicker;
  if (typeof win.showSaveFilePicker === "function") {
    try {
      const handle = await win.showSaveFilePicker({
        suggestedName,
        types: [{ description: "Markdown", accept: { "text/markdown": [".md"] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(content);
      await writable.close();
      return true;
    } catch (e) {
      if (isUserCancellation(e)) return false;
      throw e;
    }
  }
  // Fallback — no real dialog on Safari/Firefox unless the user has
  // configured "ask where to save". The file lands in the default
  // downloads folder.
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  return true;
}

export function FinalReport({
  state,
  onCompleted,
}: {
  state: RunState;
  onCompleted: (next: RunState) => void;
}) {
  const report = state.pending_payload.report ?? "";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("rendered");
  const [copied, setCopied] = useState(false);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      // Save first; if the user cancels the dialog, the run still
      // completes — they intentionally chose not to keep the file.
      await saveReportToFile(report);
      const next = await approveReport(state.run_id);
      onCompleted(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(report);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <main>
      <Stepper status={state.status} />
      <h1>Final report</h1>
      <p className="muted">
        Review the report below. <strong>Approve &amp; save</strong> opens a save dialog and then
        closes the run; cancelling the dialog finishes the run without saving.
      </p>

      <div className="tabs">
        <button
          type="button"
          className={tab === "rendered" ? "tab active" : "tab"}
          onClick={() => setTab("rendered")}
        >
          Rendered
        </button>
        <button
          type="button"
          className={tab === "raw" ? "tab active" : "tab"}
          onClick={() => setTab("raw")}
        >
          Raw Markdown
        </button>
        <div className="tab-actions">
          <button type="button" className="ghost" onClick={copy}>
            {copied ? "Copied ✓" : "Copy"}
          </button>
        </div>
      </div>

      {tab === "raw" ? (
        <pre className="report">{report}</pre>
      ) : (
        <div className="report-rendered">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeRaw]}
            components={{
              // Open every link in a new tab so clicking a citation does not
              // navigate the preview away from the report.
              a: ({ node: _node, ...props }) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
            }}
          >
            {report}
          </ReactMarkdown>
        </div>
      )}

      {err && <div className="error">{err}</div>}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={submit} disabled={busy}>
          {busy ? "Saving…" : "Approve & save…"}
        </button>
      </div>
    </main>
  );
}
