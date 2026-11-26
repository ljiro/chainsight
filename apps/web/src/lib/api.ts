import type { ApiErrorBody } from "./types";

export const API_BASE = "/api/v1";

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

/**
 * GET a ChainSight API resource. Non-2xx responses throw `ApiError` built from the backend's
 * error envelope, unless the status is listed in `acceptStatuses` (e.g. 503 from readiness,
 * whose body still describes component health).
 */
export async function apiGet<T>(
  path: string,
  { signal, acceptStatuses = [] }: { signal?: AbortSignal; acceptStatuses?: number[] } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { signal, headers: { Accept: "application/json" } });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Cannot reach the ChainSight API", 0, "network_error");
  }

  const body: unknown = await res.json().catch(() => null);
  if (res.ok || acceptStatuses.includes(res.status)) return body as T;

  if (isErrorBody(body)) {
    const e = body.error;
    throw new ApiError(e.message, res.status, e.code, e.request_id, e.details);
  }
  throw new ApiError(`Request failed (${res.status})`, res.status, "http_error");
}

/** POST JSON (or a raw body with an explicit content type) and parse the response. */
export async function apiPost<T>(
  path: string,
  body: unknown,
  { contentType = "application/json", acceptStatuses = [] }: { contentType?: string; acceptStatuses?: number[] } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": contentType },
      body: contentType === "application/json" ? JSON.stringify(body) : (body as BodyInit),
    });
  } catch {
    throw new ApiError("Cannot reach the ChainSight API", 0, "network_error");
  }
  const parsed: unknown = await res.json().catch(() => null);
  if (res.ok || acceptStatuses.includes(res.status)) return parsed as T;
  if (isErrorBody(parsed)) {
    const e = parsed.error;
    throw new ApiError(e.message, res.status, e.code, e.request_id, e.details);
  }
  throw new ApiError(`Request failed (${res.status})`, res.status, "http_error");
}
