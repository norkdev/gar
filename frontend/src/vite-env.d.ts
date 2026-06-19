/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend base URL. Empty → same-origin (Vite dev proxy / CloudFront same
   *  origin). Set to the Lambda Function URL to point a build at the cloud. */
  readonly VITE_GAR_API_URL?: string;
  /** Shared API key sent as X-GAR-API-Key. Required when the backend gate is
   *  enabled (i.e. the cloud); unset for a local backend running open. */
  readonly VITE_GAR_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
