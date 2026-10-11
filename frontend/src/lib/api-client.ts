/** Minimal fetch wrapper that turns the backend's error envelope into a typed `ApiError`. */

import { toApiError } from '@/lib/api-error';
import {
  clearSession,
  getAccessToken,
  isSessionRejection,
  refreshAccessToken,
} from '@/lib/auth-session';

export { ApiError, toApiError } from '@/lib/api-error';
export type { ApiErrorBody } from '@/lib/api-error';

/** GET a JSON resource. Throws `ApiError` for non-2xx responses. */
export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  // `HeadersInit` may be a Headers instance or tuple array, which object spread would drop.
  const headers = new Headers(init?.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiRequestOptions {
  /** Serialized as JSON. Omit for requests without a body. */
  body?: unknown;
  signal?: AbortSignal;
}

/** Auth failures that a fresh access token can fix; anything else is surfaced as-is. */
const REFRESHABLE_CODES: ReadonlySet<string> = new Set(['TOKEN_EXPIRED', 'TOKEN_INVALID']);

/** What a request carries: nothing, a JSON value, or multipart form data. */
type RequestPayload =
  | { readonly kind: 'none' }
  | { readonly kind: 'json'; readonly value: unknown }
  | { readonly kind: 'form'; readonly value: FormData };

interface AuthorizedRequest {
  readonly method: HttpMethod;
  readonly path: string;
  readonly payload: RequestPayload;
  readonly signal: AbortSignal | undefined;
  /** `Accept` header; `null` leaves the browser default (file downloads). */
  readonly accept: string | null;
}

async function send(request: AuthorizedRequest, token: string | null): Promise<Response> {
  const headers = new Headers();
  if (request.accept !== null) headers.set('Accept', request.accept);
  // Multipart bodies get no Content-Type here: the browser adds it with the boundary.
  if (request.payload.kind === 'json') headers.set('Content-Type', 'application/json');
  if (token !== null) headers.set('Authorization', `Bearer ${token}`);
  let body: BodyInit | null;
  switch (request.payload.kind) {
    case 'none':
      body = null;
      break;
    case 'json':
      body = JSON.stringify(request.payload.value);
      break;
    case 'form':
      body = request.payload.value;
      break;
    default: {
      const exhaustive: never = request.payload;
      throw new Error(`Unhandled payload: ${JSON.stringify(exhaustive)}`);
    }
  }
  return fetch(request.path, {
    method: request.method,
    headers,
    credentials: 'same-origin',
    body,
    signal: request.signal ?? null,
  });
}

/**
 * Send with the in-memory bearer token and return the successful response. On a 401 with
 * `TOKEN_EXPIRED` or `TOKEN_INVALID` it refreshes once and retries once with the same payload.
 * If the server rejects the refresh (a 401 auth code) the session is cleared and the original
 * `ApiError` is thrown. Any other refresh failure (429, 5xx, network) keeps the session and
 * throws the refresh error, so the UI can offer a retry instead of signing the user out.
 */
async function authorizedFetch(request: AuthorizedRequest): Promise<Response> {
  const usedToken = getAccessToken();
  const response = await send(request, usedToken);
  if (response.ok) return response;

  const error = await toApiError(response);
  if (error.status !== 401 || !REFRESHABLE_CODES.has(error.code)) throw error;

  // Another request may already have refreshed while this one was in flight; reuse its token
  // instead of rotating the refresh cookie a second time.
  const current = getAccessToken();
  let retryToken: string;
  if (current !== null && current !== usedToken) {
    retryToken = current;
  } else {
    try {
      retryToken = await refreshAccessToken();
    } catch (refreshError: unknown) {
      if (!isSessionRejection(refreshError)) throw refreshError;
      clearSession();
      throw error;
    }
  }

  const retried = await send(request, retryToken);
  if (!retried.ok) throw await toApiError(retried);
  return retried;
}

async function readBody<T>(response: Response): Promise<T> {
  // Callers of 204 endpoints use `T = undefined`; there is no body to parse.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Authenticated JSON request with the refresh-once behaviour of `authorizedFetch`. A 204
 * resolves to `undefined`.
 */
export async function apiRequest<T>(
  method: HttpMethod,
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const payload: RequestPayload =
    options.body === undefined ? { kind: 'none' } : { kind: 'json', value: options.body };
  const response = await authorizedFetch({
    method,
    path,
    payload,
    signal: options.signal,
    accept: 'application/json',
  });
  return readBody<T>(response);
}

export interface ApiTransferOptions {
  signal?: AbortSignal;
}

/**
 * Authenticated multipart POST of `formData`, answered with JSON. Same auth and refresh-once
 * behaviour as `apiRequest`; the retry re-sends the same `FormData`.
 */
export async function apiUpload<T>(
  path: string,
  formData: FormData,
  options: ApiTransferOptions = {},
): Promise<T> {
  const response = await authorizedFetch({
    method: 'POST',
    path,
    payload: { kind: 'form', value: formData },
    signal: options.signal,
    accept: 'application/json',
  });
  return readBody<T>(response);
}

/** Authenticated GET of a file, resolved as a `Blob`. Errors still arrive as `ApiError`. */
export async function apiDownload(path: string, options: ApiTransferOptions = {}): Promise<Blob> {
  const response = await authorizedFetch({
    method: 'GET',
    path,
    payload: { kind: 'none' },
    signal: options.signal,
    accept: null,
  });
  return response.blob();
}
