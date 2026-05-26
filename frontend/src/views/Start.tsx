// Entry view: pick idea notes via the browser file picker.
//
// Two pickers: folder (webkitdirectory, recursive .md harvest) and
// individual files (multiple .md). The user can switch between modes;
// the selection is replaced, not merged, when they pick again.
//
// On submit we read each File's contents and POST them as
// notes_content. The backend never sees a filesystem path.

import { useRef, useState } from "react";
import { createRunWithNotes } from "../lib/api";
import type { NoteInput, RunState } from "../lib/api";

interface SelectedFile {
  /** webkitRelativePath when picked via folder; bare name otherwise. */
  path: string;
  file: File;
}

export function Start({ onStarted }: { onStarted: (s: RunState) => void }) {
  const folderInputRef = useRef<HTMLInputElement>(null);
  const filesInputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<SelectedFile[]>([]);
  const [pickedSource, setPickedSource] = useState<"folder" | "files" | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleFolderPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const all = Array.from(e.target.files ?? []);
    const md = all
      .filter((f) => f.name.toLowerCase().endsWith(".md"))
      .map((f) => ({ path: f.webkitRelativePath || f.name, file: f }));
    setSelected(md);
    setPickedSource("folder");
    setErr(null);
    // Reset so picking the same folder twice re-fires onChange.
    e.target.value = "";
  };

  const handleFilesPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const all = Array.from(e.target.files ?? []);
    const md = all
      .filter((f) => f.name.toLowerCase().endsWith(".md"))
      .map((f) => ({ path: f.name, file: f }));
    setSelected(md);
    setPickedSource("files");
    setErr(null);
    e.target.value = "";
  };

  const folderName = (): string | null => {
    if (pickedSource !== "folder" || selected.length === 0) return null;
    const firstPath = selected[0].path;
    const slash = firstPath.indexOf("/");
    return slash > 0 ? firstPath.slice(0, slash) : null;
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const notes: NoteInput[] = await Promise.all(
        selected.map(async (s) => ({ path: s.path, content: await s.file.text() })),
      );
      const state = await createRunWithNotes(notes);
      onStarted(state);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main>
      <h1>Guided Agentic Retrieval</h1>
      <p className="muted">
        Survey published literature against your private idea notes. The agent gathers grounded
        candidates and stops there — the novelty judgement stays with you.
      </p>

      <h2>Idea source</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Pick a folder of Markdown notes (an Obsidian vault or a sub-folder), or a few specific{" "}
        <code>.md</code> files. Note contents are uploaded; the backend never touches your
        filesystem path.
      </p>
      <p className="subtle" style={{ marginTop: "var(--sp-2)" }}>
        Heads-up: <code>.gitignore</code> and <code>.ignore</code> are not consulted in the web UI.
        Past <code>gar-report-*.md</code> files in the picked folder will be re-uploaded unless you
        pick a sub-folder or specific files. (The CLI honors them — see README.)
      </p>

      <input
        ref={folderInputRef}
        type="file"
        // @ts-expect-error — webkitdirectory is non-standard but supported by all major browsers
        webkitdirectory=""
        directory=""
        multiple
        onChange={handleFolderPick}
        style={{ display: "none" }}
      />
      <input
        ref={filesInputRef}
        type="file"
        accept=".md,text/markdown"
        multiple
        onChange={handleFilesPick}
        style={{ display: "none" }}
      />

      <div className="row" style={{ marginTop: "var(--sp-3)" }}>
        <button
          type="button"
          className="secondary"
          onClick={() => folderInputRef.current?.click()}
          disabled={busy}
        >
          Pick folder…
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => filesInputRef.current?.click()}
          disabled={busy}
        >
          Pick file(s)…
        </button>
      </div>

      {selected.length > 0 && (
        <div
          style={{
            marginTop: "var(--sp-4)",
            padding: "var(--sp-3) var(--sp-4)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            background: "var(--color-surface-2)",
          }}
        >
          <div className="row-between" style={{ marginBottom: "var(--sp-2)" }}>
            <span className="muted">
              {pickedSource === "folder" && folderName() ? (
                <>
                  Folder: <code>{folderName()}</code> · {selected.length} Markdown file
                  {selected.length === 1 ? "" : "s"}
                </>
              ) : (
                <>
                  {selected.length} Markdown file{selected.length === 1 ? "" : "s"} selected
                </>
              )}
            </span>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setSelected([]);
                setPickedSource(null);
              }}
              disabled={busy}
            >
              Clear
            </button>
          </div>
          <ul
            className="mono"
            style={{
              listStyle: "none",
              padding: 0,
              margin: 0,
              maxHeight: "10rem",
              overflowY: "auto",
              fontSize: "var(--fs-xs)",
              color: "var(--color-text-muted)",
              lineHeight: "var(--lh-tight)",
            }}
          >
            {selected.slice(0, 50).map((s) => (
              <li key={s.path} style={{ padding: "2px 0" }}>
                {s.path}
              </li>
            ))}
            {selected.length > 50 && (
              <li className="subtle" style={{ padding: "var(--sp-1) 0" }}>
                + {selected.length - 50} more…
              </li>
            )}
          </ul>
        </div>
      )}

      {selected.length === 0 && (
        <p className="subtle" style={{ marginTop: "var(--sp-3)" }}>
          v1 reads Markdown only. Non-<code>.md</code> files in a folder are filtered out
          automatically.
        </p>
      )}

      {err && <div className="error">{err}</div>}

      <div className="row" style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={submit} disabled={busy || selected.length === 0}>
          {busy ? "Deriving concept…" : "Start"}
        </button>
        {busy && <span className="muted">The first LLM call usually takes 5–15 seconds.</span>}
      </div>
    </main>
  );
}
