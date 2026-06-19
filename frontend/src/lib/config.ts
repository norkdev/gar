// Backend connection config: base URL + API key.
//
// Empty base URL → same-origin: the Vite dev server proxies /runs/* to a local
// backend, and a CloudFront-hosted build can route /runs/* to the Function URL
// on the same origin. Set VITE_GAR_API_URL (+ VITE_GAR_API_KEY) to point a
// build directly at the cloud Function URL instead.

export const API_BASE = (import.meta.env.VITE_GAR_API_URL ?? "").replace(/\/+$/, "");
export const API_KEY = import.meta.env.VITE_GAR_API_KEY;

export function apiUrl(path: string): string {
  return API_BASE + path;
}

// Headers for every backend call: identify the surface (audit attribution) and,
// when configured, the API key the backend gate checks. `json` adds the
// Content-Type for request bodies.
export function apiHeaders(opts?: { json?: boolean }): Record<string, string> {
  const headers: Record<string, string> = { "X-GAR-Client": "web" };
  if (opts?.json) headers["Content-Type"] = "application/json";
  if (API_KEY) headers["X-GAR-API-Key"] = API_KEY;
  return headers;
}
