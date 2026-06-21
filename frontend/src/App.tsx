// Top-level router. Gates the app behind Cognito login (when configured), then
// picks a view based on the run's current status.

import { useEffect, useState } from "react";
import { ThemeToggle } from "./components/ThemeToggle";
import { isInProgress, type RunState } from "./lib/api";
import { initAuth, isAuthenticated, login, logout, userEmail } from "./lib/auth";
import { Completed } from "./views/Completed";
import { ConceptReview } from "./views/ConceptReview";
import { FinalReport } from "./views/FinalReport";
import { Login } from "./views/Login";
import { Processing } from "./views/Processing";
import { SourceSelection } from "./views/SourceSelection";
import { Start } from "./views/Start";

type AuthPhase = "loading" | "anon" | "ready";

function App() {
  const [authPhase, setAuthPhase] = useState<AuthPhase>("loading");
  const [email, setEmail] = useState<string | null>(null);
  const [state, setState] = useState<RunState | null>(null);

  useEffect(() => {
    initAuth()
      .then(({ authEnabled }) => {
        // No pool configured (local dev) → open; otherwise require a session.
        if (!authEnabled || isAuthenticated()) {
          setEmail(userEmail());
          setAuthPhase("ready");
        } else {
          setAuthPhase("anon");
        }
      })
      .catch(() => setAuthPhase("anon"));
  }, []);

  if (authPhase === "loading") {
    return (
      <>
        <ThemeToggle />
        <main>
          <p className="muted">
            <span className="spinner" aria-hidden="true" /> Loading…
          </p>
        </main>
      </>
    );
  }
  if (authPhase === "anon") {
    return (
      <>
        <ThemeToggle />
        <Login onLogin={() => void login()} />
      </>
    );
  }

  return (
    <>
      <ThemeToggle />
      {email && (
        <div className="auth-bar">
          <span className="muted">{email}</span>
          <button className="ghost" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      )}
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
