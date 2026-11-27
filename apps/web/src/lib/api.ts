import type { ApiErrorBody } from "./types";

export const API_BASE = "/api/v1";

/** Sent on every request. The API requires it on cookie-authenticated writes (CSRF defence:
 * browsers only add custom headers cross-site after a CORS preflight the API refuses). */
export const CSRF_HEADERS = { "X-Requested-With": "chainsight" } as const;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null = null,
    readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorBody).error?.message === "string"
  );
}

/** Called when the session cannot be refreshed. Default: go to the sign-in page. */
let onUnauthenticated: () => void = () => {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.assign(`/login?next=${next}`);
};

export function setUnauthenticatedHandler(handler: () => void): void {
  onUnauthenticated = handler;
}

let refreshing: Promise<boolean> | null = null;

/** Rotate the refresh cookie once, however many requests hit an expired token at once. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { Accept: "application/json", ...CSRF_HEADERS },
  })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

type RequestOptions = {
  method?: string;
  body?: BodyInit | null;
  contentType?: string;
  signal?: AbortSignal;
  acceptStatuses?: number[];
};

async function request<T>(path: string, opts: RequestOptions, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...CSRF_HEADERS };
  if (opts.contentType) headers["Content-Type"] = opts.contentType;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body ?? undefined,
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Cannot reach the ChainSight API", 0, "network_error");
  }

  if (res.status === 401 && !path.startsWith("/auth/")) {
    if (!retried && (await refreshSession())) return request<T>(path, opts, true);
    onUnauthenticated();
  }

  if (res.status === 204) return undefined as T;
  const body: unknown = await res.json().catch(() => null);
  if (res.ok || opts.acceptStatuses?.includes(res.status)) return body as T;

  if (isErrorBody(body)) {
    const e = body.error;
    throw new ApiError(e.message, res.status, e.code, e.request_id, e.details);
  }
  throw new ApiError(`Request failed (${res.status})`, res.status, "http_error");
}

/**
 * GET a ChainSight API resource. Non-2xx responses throw `ApiError` built from the backend's
 * error envelope, unless the status is listed in `acceptStatuses` (e.g. 503 from readiness,
 * whose body still describes component health).
 */
export function apiGet<T>(
  path: string,
  { signal, acceptStatuses = [] }: { signal?: AbortSignal; acceptStatuses?: number[] } = {},
): Promise<T> {
  return request<T>(path, { signal, acceptStatuses });
}

/** POST JSON (or a raw body with an explicit content type) and parse the response. */
export function apiPost<T>(
  path: string,
  body: unknown,
  { contentType = "application/json", acceptStatuses = [] }: { contentType?: string; acceptStatuses?: number[] } = {},
): Promise<T> {
  return request<T>(path, {
    method: "POST",
    contentType,
    acceptStatuses,
    body: contentType === "application/json" ? JSON.stringify(body) : (body as BodyInit),
  });
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", contentType: "application/json", body: JSON.stringify(body) });
}

export function apiDelete(path: string): Promise<void> {
  return request<void>(path, { method: "DELETE" });
}
