// Runtime configuration, fetched once from /config.json (written into the S3
// bucket at deploy time by FrontendStack). Keeping it out of the build means
// the static bundle carries no account-specific ids, and the same artifact
// runs against any deployment.
//
// Local dev has no config.json (the Vite dev server returns index.html / 404),
// so we fall back to "same-origin, no auth": the dev proxy reaches a local
// backend that runs with auth disabled.

export interface CognitoConfig {
  authority: string;
  hostedUiDomain: string;
  clientId: string;
  scope: string;
}

export interface RuntimeConfig {
  apiUrl: string;
  cognito: CognitoConfig | null;
}

const LOCAL: RuntimeConfig = { apiUrl: "", cognito: null };

let cached: RuntimeConfig | null = null;

export async function loadConfig(): Promise<RuntimeConfig> {
  if (cached) return cached;
  try {
    const resp = await fetch("/config.json", { cache: "no-store" });
    // A dev server serving index.html for /config.json yields HTML, not JSON —
    // guard on the content type so we don't mis-parse it as config.
    const ctype = resp.headers.get("content-type") ?? "";
    if (resp.ok && ctype.includes("application/json")) {
      cached = (await resp.json()) as RuntimeConfig;
      return cached;
    }
  } catch {
    /* no config.json → local dev */
  }
  cached = LOCAL;
  return cached;
}
