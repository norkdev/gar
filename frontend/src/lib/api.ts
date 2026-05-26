// Typed client for the gar-backend HTTP API.
//
// All URLs are relative; the Vite dev server proxies /runs/* to the backend
// (see vite.config.ts). In production, the static frontend is served behind
// the same origin as the API or via CloudFront with an explicit origin route.

export type RunStatus =
  | 'deriving_concept'
  | 'awaiting_concept_approval'
  | 'searching'
  | 'awaiting_source_selection'
  | 'evaluating'
  | 'awaiting_report_approval'
  | 'completed'
  | 'failed'

export interface Candidate {
  source_name: string
  external_id: string
  title: string
  snippet: string
  authors: string[]
  published: string | null
  url: string
  citation_anchor: string | null
}

export interface RunState {
  run_id: string
  tenant_id: string
  status: RunStatus
  context: Record<string, unknown>
  pending_payload: {
    concept?: string
    candidates?: Candidate[]
    report?: string
  }
  adopted_source_ids: string[]
  error: string | null
  updated_at: string
  // The /gates/report response includes the saved file path; piggyback here.
  saved_path?: string
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${body}`)
  }
  return response.json() as Promise<T>
}

export function createRun(vaultPath: string): Promise<RunState> {
  return fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vault_path: vaultPath }),
  }).then((r) => jsonOrThrow<RunState>(r))
}

export function getRun(runId: string): Promise<RunState> {
  return fetch(`/runs/${runId}`).then((r) => jsonOrThrow<RunState>(r))
}

export function approveConcept(
  runId: string,
  editedConcept?: string,
): Promise<RunState> {
  return fetch(`/runs/${runId}/gates/concept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(
      editedConcept !== undefined ? { edited_concept: editedConcept } : {},
    ),
  }).then((r) => jsonOrThrow<RunState>(r))
}

export function selectSources(
  runId: string,
  adoptedSourceIds: string[],
): Promise<RunState> {
  return fetch(`/runs/${runId}/gates/sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ adopted_source_ids: adoptedSourceIds }),
  }).then((r) => jsonOrThrow<RunState>(r))
}

export function approveReport(runId: string): Promise<RunState> {
  return fetch(`/runs/${runId}/gates/report`, { method: 'POST' }).then((r) =>
    jsonOrThrow<RunState>(r),
  )
}

export function candidateCompositeId(c: Candidate): string {
  return `${c.source_name}:${c.external_id}`
}
