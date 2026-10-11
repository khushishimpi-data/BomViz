/** Typed error for non-2xx API responses, built from the backend's error envelope. */

export interface ApiErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>[];
  request_id?: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>[];
  readonly requestId: string | null;
  /** Seconds to wait before retrying, from the `Retry-After` header (429/503), if present. */
  readonly retryAfterSeconds: number | null;

  constructor(status: number, body: ApiErrorBody, retryAfterSeconds: number | null = null) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.details = body.details;
    this.requestId = body.request_id ?? null;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

const FALLBACK_MESSAGE = 'The server could not complete the request. Please try again.';

function isErrorEnvelope(value: unknown): value is { error: ApiErrorBody } {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false;
  const { error } = value;
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    'message' in error &&
    typeof error.code === 'string' &&
    typeof error.message === 'string'
  );
}

/**
 * Parse a `Retry-After` header: either delta-seconds or an HTTP date. Returns whole seconds
 * (never negative), or `null` when the header is missing or malformed.
 */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number.parseInt(trimmed, 10);
  // An HTTP date always names a day and month; this stops `Date.parse` reading "-5" as a year.
  if (!/[a-z]/i.test(trimmed)) return null;
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

/** Convert a non-2xx response into an `ApiError`, never exposing raw server output. */
export async function toApiError(response: Response): Promise<ApiError> {
  const retryAfterSeconds = parseRetryAfter(response.headers.get('Retry-After'));
  const body: unknown = await response.json().catch(() => null);
  if (isErrorEnvelope(body)) {
    return new ApiError(
      response.status,
      { ...body.error, details: body.error.details },
      retryAfterSeconds,
    );
  }
  return new ApiError(
    response.status,
    {
      code: 'HTTP_ERROR',
      message: FALLBACK_MESSAGE,
      details: [],
      request_id: response.headers.get('X-Request-ID'),
    },
    retryAfterSeconds,
  );
}
