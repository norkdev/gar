// Trigger a browser download of text as a file (used by the session list and
// the completed-report view). The FinalReport gate uses the richer
// showSaveFilePicker flow; this is the plain fallback for already-saved reports.

export function downloadText(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function reportFilename(): string {
  const today = new Date().toISOString().slice(0, 10);
  return `gar-report-${today}.md`;
}
