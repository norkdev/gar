// Cognito login via oidc-client-ts (authorization-code + PKCE).
//
// The browser requests an access token carrying the `gar-api/access` scope, so
// the backend verifies it exactly like an M2M token — one auth path (D-206).
// When no Cognito config is present (local dev) auth is disabled: the app runs
// open against a local backend.
//
// Cognito's OIDC discovery is quirky, so the OAuth endpoints are supplied
// explicitly from the Hosted-UI domain rather than fetched from .well-known.

import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { loadConfig } from "./runtimeConfig";

let manager: UserManager | null = null;
let currentUser: User | null = null;

/** Initialize auth. Returns whether a login is required (cloud) or not (local).
 *  Completes a redirect callback if we just returned from the Hosted UI. */
export async function initAuth(): Promise<{ authEnabled: boolean }> {
  const cfg = await loadConfig();
  if (!cfg.cognito) return { authEnabled: false };
  const c = cfg.cognito;
  const redirect = window.location.origin + "/";

  manager = new UserManager({
    authority: c.authority,
    client_id: c.clientId,
    redirect_uri: redirect,
    post_logout_redirect_uri: redirect,
    response_type: "code",
    scope: c.scope,
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    metadata: {
      issuer: c.authority,
      authorization_endpoint: `${c.hostedUiDomain}/oauth2/authorize`,
      token_endpoint: `${c.hostedUiDomain}/oauth2/token`,
      userinfo_endpoint: `${c.hostedUiDomain}/oauth2/userInfo`,
      jwks_uri: `${c.authority}/.well-known/jwks.json`,
    },
  });

  const params = new URLSearchParams(window.location.search);
  if (params.has("code") && params.has("state")) {
    currentUser = await manager.signinRedirectCallback();
    // Drop the ?code&state from the URL so a refresh doesn't re-trigger it.
    window.history.replaceState({}, document.title, window.location.pathname);
  } else {
    currentUser = await manager.getUser();
  }
  return { authEnabled: true };
}

export function isAuthenticated(): boolean {
  return currentUser != null && !currentUser.expired;
}

export function accessToken(): string | null {
  return currentUser?.access_token ?? null;
}

export function userEmail(): string | null {
  const email = currentUser?.profile?.email;
  return typeof email === "string" ? email : null;
}

export async function login(): Promise<void> {
  await manager?.signinRedirect();
}

/** Clear the local session and redirect to Cognito's (non-standard) logout. */
export async function logout(): Promise<void> {
  const cfg = await loadConfig();
  await manager?.removeUser();
  currentUser = null;
  if (cfg.cognito) {
    const c = cfg.cognito;
    const back = encodeURIComponent(window.location.origin + "/");
    window.location.href = `${c.hostedUiDomain}/logout?client_id=${c.clientId}&logout_uri=${back}`;
  }
}
