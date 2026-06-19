// Typed client for the gar-backend HTTP API.
//
// Requests go through apiUrl/apiHeaders (lib/config): same-origin by default
// (the Vite dev server proxies /runs/* to a local backend), or pointed at the
// cloud Function URL with VITE_GAR_API_URL + VITE_GAR_API_KEY. Each call sends
// X-GAR-Client (audit attribution) and, when configured, X-GAR-API-Key.

import { apiHeaders, apiUrl } from "./config";

export type RunStatus =
  | "deriving_concept"
  | "awaiting_concept_approval"
  | "searching"
  | "awaiting_source_selection"
  | "evaluating"
  | "awaiting_report_approval"
  | "completed"
  | "failed";

export interface Candidate {
  source_name: string;
  external_id: string;
  title: string;
  snippet: string;
  authors: string[];
  published: string | null;
  url: string;
  citation_anchor: string | null;
  // Index of the semantic direction (cluster) this candidate fell in, or
  // null/undefined when directions weren't computed (BM25 mode) or it was
  // dropped as cluster noise. See `Direction` and context.directions.
  direction?: number | null;
}

// One semantic cluster of the candidate pool (embedding directions, slice 3),
// carried in run context. Used to group the sources gate so the human reviews
// by relevance cluster instead of one long flat list.
export interface Direction {
  id: number;
  representatives: string[];
  size: number;
  contains_concept: boolean;
}

export interface RunState {
  run_id: string;
  tenant_id: string;
  status: RunStatus;
  context: Record<string, unknown>;
  pending_payload: {
    concept?: string;
    candidates?: Candidate[];
    report?: string;
  };
  adopted_source_ids: string[];
  error: string | null;
  updated_at: string;
  // The /gates/report response includes the saved file path; piggyback here.
  saved_path?: string;
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export interface NoteInput {
  /** Display label for the note. Typically the file's webkitRelativePath
   *  (with the picked folder's name as prefix) or the bare filename. */
  path: string;
  content: string;
}

/** Start a run from uploaded note contents (picker flow).
 *
 *  The backend also accepts `{ vault_path }` for CLI / local-mode uses,
 *  but the browser UI uses content uploads exclusively — the picker
 *  cannot resolve absolute filesystem paths in any portable way.
 */
export function createRunWithNotes(notes: NoteInput[]): Promise<RunState> {
  return fetch(apiUrl("/runs"), {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ notes_content: notes }),
  }).then((r) => jsonOrThrow<RunState>(r));
}

export function getRun(runId: string, signal?: AbortSignal): Promise<RunState> {
  return fetch(apiUrl(`/runs/${runId}`), { headers: apiHeaders(), signal }).then((r) =>
    jsonOrThrow<RunState>(r),
  );
}

export function approveConcept(runId: string, editedConcept?: string): Promise<RunState> {
  return fetch(apiUrl(`/runs/${runId}/gates/concept`), {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify(editedConcept !== undefined ? { edited_concept: editedConcept } : {}),
  }).then((r) => jsonOrThrow<RunState>(r));
}

export function selectSources(runId: string, adoptedSourceIds: string[]): Promise<RunState> {
  return fetch(apiUrl(`/runs/${runId}/gates/sources`), {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ adopted_source_ids: adoptedSourceIds }),
  }).then((r) => jsonOrThrow<RunState>(r));
}

export function approveReport(runId: string): Promise<RunState> {
  return fetch(apiUrl(`/runs/${runId}/gates/report`), {
    method: "POST",
    headers: apiHeaders(),
  }).then((r) => jsonOrThrow<RunState>(r));
}

// In-progress statuses: the agent is working a segment; the UI polls getRun
// until it settles on a gate (awaiting_*) or a terminal status.
const IN_PROGRESS: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "deriving_concept",
  "searching",
  "evaluating",
]);

export function isInProgress(status: RunStatus): boolean {
  return IN_PROGRESS.has(status);
}

export function candidateCompositeId(c: Candidate): string {
  return `${c.source_name}:${c.external_id}`;
}
