// Gate 3: review the composed report and approve to save to disk.
//
// The report is composed Markdown with linkified citations:
//   - Inline citations link to anchor targets inside the References section.
//   - Reference entries link out to the source's canonical URL.
// We render it with react-markdown + GFM, allowing the raw `<a id="...">`
// anchor tags the backend's linkifier emits (the content is server-trusted,
// not user-supplied HTML).

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { approveReport } from "../lib/api";
import type { RunState } from "../lib/api";

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
  const [showSource, setShowSource] = useState(false);

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

  return (
    <main>
      <h1>Gate 3 — Final report</h1>
      <p className="muted">
        Review the report below. Approving saves it as a Markdown file in your vault and appends the
        filename to <code>.ignore</code> so re-runs skip it.{" "}
        <button
          type="button"
          className="secondary"
          style={{ padding: "0.2rem 0.5rem", fontSize: "0.8rem" }}
          onClick={() => setShowSource((s) => !s)}
        >
          {showSource ? "Show rendered" : "Show raw Markdown"}
        </button>
      </p>

      {showSource ? (
        <pre className="report">{report}</pre>
      ) : (
        <div className="report-rendered">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeRaw]}
            components={{
              // Open every link in a new tab so clicking a citation does not
              // navigate the preview away from the report (the user would
              // otherwise lose unsaved review state).
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

      <p style={{ marginTop: "1rem" }}>
        <button onClick={submit} disabled={busy}>
          {busy ? "Saving…" : "Approve & save"}
        </button>
      </p>
    </main>
  );
}
