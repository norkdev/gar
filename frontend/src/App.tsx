// Top-level router. Picks a view based on the run's current status.

import { useState } from 'react'
import type { RunState } from './lib/api'
import { Completed } from './views/Completed'
import { ConceptReview } from './views/ConceptReview'
import { FinalReport } from './views/FinalReport'
import { SourceSelection } from './views/SourceSelection'
import { Start } from './views/Start'

function App() {
  const [state, setState] = useState<RunState | null>(null)

  if (state === null) {
    return <Start onStarted={setState} />
  }
  switch (state.status) {
    case 'awaiting_concept_approval':
      return <ConceptReview state={state} onAdvanced={setState} />
    case 'awaiting_source_selection':
      return <SourceSelection state={state} onAdvanced={setState} />
    case 'awaiting_report_approval':
      return <FinalReport state={state} onCompleted={setState} />
    case 'completed':
    case 'failed':
      return <Completed state={state} onRestart={() => setState(null)} />
    default:
      // deriving_concept / searching / evaluating: these are in-progress
      // statuses that should be transient because the previous POST blocked
      // until a gate. Treat as an unexpected interim and offer to retry.
      return (
        <main>
          <h1>Unexpected status: {state.status}</h1>
          <p className="muted">
            The agent reported an intermediate state instead of a gate. This
            can happen if a server-side timeout cut off a phase. The run still
            exists; you can refresh later via GET /runs/{state.run_id}.
          </p>
          <p style={{ marginTop: '1rem' }}>
            <button
              className="secondary"
              onClick={() => setState(null)}
            >
              Start a new run
            </button>
          </p>
        </main>
      )
  }
}

export default App
