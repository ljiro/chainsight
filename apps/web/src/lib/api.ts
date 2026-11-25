import type { ApiErrorBody } from "./types";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null = null,
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
    throw new ApiError(body.error.message, res.status, body.error.code, body.error.request_id);
  }
  throw new ApiError(`Request failed (${res.status})`, res.status, "http_error");
}
