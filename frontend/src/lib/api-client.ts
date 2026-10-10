/** Minimal fetch wrapper that turns the backend's error envelope into a typed `ApiError`. */

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

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.details = body.details;
    this.requestId = body.request_id ?? null;
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

/** Convert a non-2xx response into an `ApiError`, never exposing raw server output. */
export async function toApiError(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  if (isErrorEnvelope(body)) {
    return new ApiError(response.status, { ...body.error, details: body.error.details });
  }
  return new ApiError(response.status, {
    code: 'HTTP_ERROR',
    message: FALLBACK_MESSAGE,
    details: [],
    request_id: response.headers.get('X-Request-ID'),
  });
}

/** GET a JSON resource. Throws `ApiError` for non-2xx responses. */
export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  // `HeadersInit` may be a Headers instance or tuple array, which object spread would drop.
  const headers = new Headers(init?.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}
