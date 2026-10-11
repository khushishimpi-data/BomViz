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

async function send(
  method: HttpMethod,
  path: string,
  options: ApiRequestOptions,
  token: string | null,
): Promise<Response> {
  const headers = new Headers({ Accept: 'application/json' });
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (token !== null) headers.set('Authorization', `Bearer ${token}`);
  return fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: options.body === undefined ? null : JSON.stringify(options.body),
    signal: options.signal ?? null,
  });
}

async function readBody<T>(response: Response): Promise<T> {
  // Callers of 204 endpoints use `T = undefined`; there is no body to parse.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Authenticated JSON request. Attaches the in-memory bearer token; on a 401 with
 * `TOKEN_EXPIRED` or `TOKEN_INVALID` it refreshes once and retries once. If the server rejects
 * the refresh (a 401 auth code) the session is cleared and the original `ApiError` is thrown.
 * Any other refresh failure (429, 5xx, network) keeps the session and throws the refresh error,
 * so the UI can offer a retry instead of signing the user out. A 204 resolves to `undefined`.
 */
export async function apiRequest<T>(
  method: HttpMethod,
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const usedToken = getAccessToken();
  const response = await send(method, path, options, usedToken);
  if (response.ok) return readBody<T>(response);

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

  const retried = await send(method, path, options, retryToken);
  if (!retried.ok) throw await toApiError(retried);
  return readBody<T>(retried);
}
