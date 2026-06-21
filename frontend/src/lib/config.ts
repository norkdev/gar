// Backend connection: base URL from runtime config, bearer token from auth.
//
// Empty base URL → same-origin: the Vite dev server proxies /runs/* to a local
// backend (auth disabled). In the cloud, config.json carries the Function URL
// and the request is authenticated with the Cognito access token.

import { accessToken } from "./auth";
import { loadConfig } from "./runtimeConfig";

export async function apiUrl(path: string): Promise<string> {
  const { apiUrl } = await loadConfig();
  return apiUrl.replace(/\/+$/, "") + path;
}

// Headers for every backend call: identify the surface (audit attribution) and,
// when signed in, the Cognito bearer token the backend gate verifies. `json`
// adds the Content-Type for request bodies.
export function apiHeaders(opts?: { json?: boolean }): Record<string, string> {
  const headers: Record<string, string> = { "X-GAR-Client": "web" };
  if (opts?.json) headers["Content-Type"] = "application/json";
  const token = accessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}
