/**
 * In-memory access-token store. The token is never persisted (no localStorage/sessionStorage):
 * a reload restores the session through the HttpOnly refresh cookie via `refreshAccessToken()`.
 */

import { ApiError, toApiError } from '@/lib/api-error';

export interface SessionChange {
  /** The new access token, or `null` when the session ended. */
  readonly token: string | null;
  /**
   * True when a token replaced a previous one that belongs to a different user (the JWT `sub`
   * changed without passing through signed-out), e.g. another tab signed in as someone else
   * and this tab's refresh picked up their cookie. Cached data of the previous user must go.
   */
  readonly userChanged: boolean;
}

export type SessionListener = (change: SessionChange) => void;

export const REFRESH_PATH = '/api/v1/auth/refresh';
const REFRESH_LOCK_NAME = 'bomviz-refresh';

/** Refresh rejections that mean the refresh cookie is gone for good, so the session ends. */
const SESSION_ENDING_CODES: ReadonlySet<string> = new Set([
  'UNAUTHENTICATED',
  'TOKEN_INVALID',
  'TOKEN_EXPIRED',
  'TOKEN_REUSED',
]);

let accessToken: string | null = null;
let accessSubject: string | null = null;
let inFlightRefresh: Promise<string> | null = null;
const listeners = new Set<SessionListener>();

function notify(userChanged: boolean): void {
  const change: SessionChange = { token: accessToken, userChanged };
  for (const listener of listeners) listener(change);
}

function decodeBase64Url(segment: string): string {
  const base64 = segment.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/**
 * Read the `sub` claim from a JWT payload without verifying it. This is identity bookkeeping
 * only (detecting that the token now belongs to someone else); the server remains the sole
 * authority on whether a token is valid. Returns `null` for anything that is not a JWT with a
 * string `sub`.
 */
export function decodeSubject(token: string): string | null {
  const payload = token.split('.')[1];
  if (payload === undefined || payload === '') return null;
  try {
    const claims: unknown = JSON.parse(decodeBase64Url(payload));
    if (typeof claims !== 'object' || claims === null || !('sub' in claims)) return null;
    return typeof claims.sub === 'string' && claims.sub !== '' ? claims.sub : null;
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function setSession(token: string): void {
  const subject = decodeSubject(token);
  const userChanged = accessToken !== null && subject !== accessSubject;
  accessToken = token;
  accessSubject = subject;
  notify(userChanged);
}

export function clearSession(): void {
  if (accessToken === null) return;
  accessToken = null;
  accessSubject = null;
  notify(false);
}

/** Register a listener for token changes. Returns an unsubscribe function. */
export function subscribe(listener: SessionListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * True when `error` is the server refusing the refresh cookie itself (401 with an auth code).
 * Rate limiting, server errors, network failures and malformed responses are not: the cookie
 * may still be valid, so the session is kept and the caller can retry.
 */
export function isSessionRejection(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401 && SESSION_ENDING_CODES.has(error.code);
}

/**
 * Run `task` while holding the cross-tab `bomviz-refresh` Web Lock, so it never overlaps a
 * refresh (or logout) in another tab. Web Locks is missing in older browsers and jsdom; there
 * the task runs directly and only the in-tab guards apply.
 */
export async function withRefreshLock<T>(task: () => Promise<T>): Promise<T> {
  if (!('locks' in navigator)) return task();
  return await navigator.locks.request(REFRESH_LOCK_NAME, task);
}

function parseAccessToken(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('access_token' in body)) return null;
  return typeof body.access_token === 'string' && body.access_token !== ''
    ? body.access_token
    : null;
}

async function requestRefresh(): Promise<string> {
  const response = await fetch(REFRESH_PATH, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    const error = await toApiError(response);
    // Only an auth rejection proves the refresh cookie is dead; anything else is transient.
    if (isSessionRejection(error)) clearSession();
    throw error;
  }
  const token = parseAccessToken(await response.json().catch(() => null));
  if (token === null) throw new Error('Refresh response did not contain an access token.');
  setSession(token);
  return token;
}

/**
 * Exchange the refresh cookie for a new access token.
 *
 * Single-flight within the tab: concurrent callers share one request. Across tabs the refresh
 * is serialized with the Web Locks API when available, because rotation revokes the presented
 * cookie and two tabs refreshing at once would look like token reuse to the server.
 *
 * Resolves with the new token. Rejects with `ApiError` when the server refuses; the session is
 * cleared only for a 401 auth rejection (see `isSessionRejection`). 429, 5xx, network errors and
 * malformed responses reject without touching the session.
 */
export function refreshAccessToken(): Promise<string> {
  if (inFlightRefresh !== null) return inFlightRefresh;

  inFlightRefresh = withRefreshLock(requestRefresh).finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}
