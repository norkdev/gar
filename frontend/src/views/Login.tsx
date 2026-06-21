// Sign-in screen (shown when a Cognito pool is configured and there's no
// session). The button redirects to the Cognito Hosted UI.

export function Login({ onLogin }: { onLogin: () => void }) {
  return (
    <main className="has-corner-logo">
      <img className="brand-logo" src="/gar.png" alt="GAR" />
      <h1>GAR — Guided Agentic Retrieval</h1>
      <p className="muted">
        Survey published literature against your own in-progress idea — grounded, cited, and
        human-approved at every gate. Sign in to begin.
      </p>
      <p style={{ marginTop: "var(--sp-5)" }}>
        <button onClick={onLogin}>Sign in</button>
      </p>
    </main>
  );
}
