// EventSource hook for the /runs/{id}/events stream.
//
// The stream emits three event types:
//   - "state": full RunState snapshot
//   - "audit": one AuditRecord (matched by run_id)
//   - "done":  signals the run reached a gate or terminal status
//
// useRunStream caches the latest state and a rolling list of audit events
// for display in loading screens.

import { useEffect, useState } from 'react'
import type { RunState } from './api'

export interface AuditEvent {
  schema_version: string
  timestamp: string
  run_id: string
  tenant_id: string
  tool_name: string
  input: Record<string, unknown>
  output: Record<string, unknown> | null
  duration_ms: number | null
  status: 'ok' | 'error'
  error: string | null
}

export interface StreamSnapshot {
  state: RunState | null
  events: AuditEvent[]
  done: boolean
}

export function useRunStream(runId: string | null): StreamSnapshot {
  const [snapshot, setSnapshot] = useState<StreamSnapshot>({
    state: null,
    events: [],
    done: false,
  })

  useEffect(() => {
    if (!runId) {
      setSnapshot({ state: null, events: [], done: false })
      return
    }
    const url = `/runs/${runId}/events`
    const es = new EventSource(url)

    es.addEventListener('state', (e) => {
      const data = JSON.parse((e as MessageEvent).data) as RunState
      setSnapshot((prev) => ({ ...prev, state: data }))
    })
    es.addEventListener('audit', (e) => {
      const data = JSON.parse((e as MessageEvent).data) as AuditEvent
      setSnapshot((prev) => ({ ...prev, events: [...prev.events, data] }))
    })
    es.addEventListener('done', () => {
      setSnapshot((prev) => ({ ...prev, done: true }))
      es.close()
    })
    es.onerror = () => {
      // Browser auto-reconnects by default. If the run is already done the
      // server closes the connection cleanly, so suppress noisy errors.
    }

    return () => es.close()
  }, [runId])

  return snapshot
}
