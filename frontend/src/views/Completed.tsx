// Terminal screen after gate 3 approval (or a FAILED run). Also the view when
// a completed session is opened from the session list — so it renders the
// retained report (D-204) with copy / download.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { Stepper } from "../components/Stepper";
import type { RunState } from "../lib/api";
import { downloadText, reportFilename } from "../lib/download";

export function Completed({ state, onRestart }: { state: RunState; onRestart: () => void }) {
  const failed = state.status === "failed";
  // saved_path is only set in vault mode (local CLI use).
  const hasSavedPath = Boolean(state.saved_path);
  const report = (state.context.report as string | undefined) ?? "";
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(report);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <main>
      <Stepper status={state.status} />
      <h1>{failed ? "Run failed" : "Done"}</h1>

      {failed ? (
        <div className="error">{state.error ?? "Unknown failure."}</div>
      ) : hasSavedPath ? (
        <>
          <p>Report saved at:</p>
          <div className="saved-path">{state.saved_path}</div>
          <p className="muted">
            The filename has been appended to <code>.ignore</code> in the same folder, so future
            runs will skip it.
          </p>
        </>
      ) : report ? (
        <>
          <div className="row" style={{ marginBottom: "var(--sp-3)" }}>
            <button type="button" className="ghost" onClick={copy}>
              {copied ? "Copied ✓" : "Copy"}
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => downloadText(report, reportFilename())}
            >
              Download
            </button>
          </div>
          <div className="report-rendered">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeRaw]}
              components={{
                a: ({ node: _node, ...props }) => (
                  <a {...props} target="_blank" rel="noopener noreferrer" />
                ),
              }}
            >
              {report}
            </ReactMarkdown>
          </div>
        </>
      ) : (
        <p className="muted">The report was approved.</p>
      )}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={onRestart} className="secondary">
          Start a new run
        </button>
      </div>
    </main>
  );
}
