// Top-level router. Picks a view based on the run's current status.

import { useState } from "react";
import { ThemeToggle } from "./components/ThemeToggle";
import { isInProgress, type RunState } from "./lib/api";
import { Completed } from "./views/Completed";
import { ConceptReview } from "./views/ConceptReview";
import { FinalReport } from "./views/FinalReport";
import { Processing } from "./views/Processing";
import { SourceSelection } from "./views/SourceSelection";
import { Start } from "./views/Start";

function App() {
  const [state, setState] = useState<RunState | null>(null);

  return (
    <>
      <ThemeToggle />
      {renderView(state, setState)}
    </>
  );
}

function renderView(state: RunState | null, setState: (s: RunState | null) => void) {
  if (state === null) {
    return <Start onStarted={setState} />;
  }
  switch (state.status) {
    case "awaiting_concept_approval":
      return <ConceptReview state={state} onAdvanced={setState} />;
    case "awaiting_source_selection":
      return <SourceSelection state={state} onAdvanced={setState} />;
    case "awaiting_report_approval":
      return <FinalReport state={state} onCompleted={setState} />;
    case "completed":
    case "failed":
      return <Completed state={state} onRestart={() => setState(null)} />;
    default:
      // deriving_concept / searching / evaluating: a segment is running
      // server-side. Poll until it settles on the next gate or a terminal
      // status, then re-render the matching view above.
      if (isInProgress(state.status)) {
        return <Processing state={state} onAdvanced={setState} onRestart={() => setState(null)} />;
      }
      return (
        <main>
          <h1>Unexpected status: {state.status}</h1>
          <p style={{ marginTop: "1rem" }}>
            <button className="secondary" onClick={() => setState(null)}>
              Start a new run
            </button>
          </p>
        </main>
      );
  }
}

export default App;
