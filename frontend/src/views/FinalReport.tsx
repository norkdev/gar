// Gate 3: review the composed report and approve to save to disk.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { Stepper } from "../components/Stepper";
import { approveReport } from "../lib/api";
import type { RunState } from "../lib/api";

type Tab = "rendered" | "raw";

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

  const download = () => {
    const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gar-report-${state.run_id}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <main>
      <Stepper status={state.status} />
      <h1>Final report</h1>
      <p className="muted">
        Review the report below. Use Copy or Download to keep the Markdown locally, then Approve
        &amp; finish to close the run.
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
          <button type="button" className="ghost" onClick={download}>
            Download
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
          {busy ? "Finishing…" : "Approve & finish"}
        </button>
      </div>
    </main>
  );
}
