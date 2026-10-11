import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { buildAccessToken } from '@/tests/auth';
import { server } from '@/tests/server';

import { ApiError } from './api-client';
import {
  clearSession,
  decodeSubject,
  getAccessToken,
  isSessionRejection,
  refreshAccessToken,
  setSession,
  subscribe,
  withRefreshLock,
} from './auth-session';

function buildRefreshResponse(accessToken = 'fresh-token') {
  return { access_token: accessToken, token_type: 'bearer', expires_in: 900 };
}

function errorEnvelope(code: string, message: string) {
  return { error: { code, message, details: [], request_id: 'req-1' } };
}

afterEach(() => {
  clearSession();
});

describe('session store', () => {
  it('starts empty and stores, then clears, the access token', () => {
    expect(getAccessToken()).toBeNull();

    setSession('abc');
    expect(getAccessToken()).toBe('abc');

    clearSession();
    expect(getAccessToken()).toBeNull();
  });

  it('notifies subscribers of changes until they unsubscribe', () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);

    setSession('abc');
    clearSession();
    unsubscribe();
    setSession('def');

    expect(listener.mock.calls).toEqual([
      [{ token: 'abc', userChanged: false }],
      [{ token: null, userChanged: false }],
    ]);
  });

  it('flags a user change when the token subject changes without signing out', () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);

    setSession(buildAccessToken('user-a'));
    setSession(buildAccessToken('user-a'));
    setSession(buildAccessToken('user-b'));
    clearSession();
    setSession(buildAccessToken('user-a'));
    unsubscribe();

    expect(
      listener.mock.calls.map(([change]) => (change as { userChanged: boolean }).userChanged),
    ).toEqual([false, false, true, false, false]);
  });
});

describe('decodeSubject', () => {
  it('reads sub from a JWT payload', () => {
    expect(decodeSubject(buildAccessToken('3f2c9a1e-user'))).toBe('3f2c9a1e-user');
  });

  it('decodes base64url payloads that use - and _', () => {
    // This payload encodes with both "+" and "/" in standard base64, so both substitutions apply.
    const payload = btoa(JSON.stringify({ sub: '~~~?' }))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
    expect(decodeSubject(`header.${payload}.sig`)).toBe('~~~?');
  });

  it.each([
    ['an opaque token', 'test-access-token'],
    ['a payload that is not base64', 'header.%%%.sig'],
    ['a payload that is not JSON', `header.${btoa('not json')}.sig`],
    ['a payload without sub', `header.${btoa(JSON.stringify({ typ: 'access' }))}.sig`],
    ['a non-string sub', `header.${btoa(JSON.stringify({ sub: 42 }))}.sig`],
  ])('returns null for %s', (_label, token) => {
    expect(decodeSubject(token)).toBeNull();
  });
});

describe('refreshAccessToken', () => {
  it('POSTs to the refresh endpoint with same-origin credentials and stores the token', async () => {
    server.use(http.post('*/api/v1/auth/refresh', () => HttpResponse.json(buildRefreshResponse())));
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    await expect(refreshAccessToken()).resolves.toBe('fresh-token');

    expect(getAccessToken()).toBe('fresh-token');
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/auth/refresh',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    );
  });

  it('shares one request between concurrent callers', async () => {
    let calls = 0;
    server.use(
      http.post('*/api/v1/auth/refresh', () => {
        calls += 1;
        return HttpResponse.json(buildRefreshResponse(`token-${calls}`));
      }),
    );

    const results = await Promise.all([refreshAccessToken(), refreshAccessToken()]);

    expect(calls).toBe(1);
    expect(results).toEqual(['token-1', 'token-1']);
  });

  it('starts a new request once the previous one has settled', async () => {
    let calls = 0;
    server.use(
      http.post('*/api/v1/auth/refresh', () => {
        calls += 1;
        return HttpResponse.json(buildRefreshResponse(`token-${calls}`));
      }),
    );

    await refreshAccessToken();
    await expect(refreshAccessToken()).resolves.toBe('token-2');
    expect(calls).toBe(2);
  });

  it('rejects with ApiError and clears the session when the server refuses', async () => {
    setSession('stale');
    server.use(
      http.post('*/api/v1/auth/refresh', () =>
        HttpResponse.json(errorEnvelope('TOKEN_REUSED', 'Session ended.'), { status: 401 }),
      ),
    );

    const error: unknown = await refreshAccessToken().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: 'TOKEN_REUSED' });
    expect(getAccessToken()).toBeNull();
  });

  it.each([
    ['429 rate limited', 429, 'RATE_LIMITED'],
    ['503 unavailable', 503, 'SERVICE_UNAVAILABLE'],
    ['401 with a non-auth code', 401, 'SOMETHING_ELSE'],
  ])('keeps the session when the refresh fails with %s', async (_label, status, code) => {
    setSession('still-valid');
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    server.use(
      http.post('*/api/v1/auth/refresh', () =>
        HttpResponse.json(errorEnvelope(code, 'Try again.'), { status }),
      ),
    );

    const error: unknown = await refreshAccessToken().catch((caught: unknown) => caught);
    unsubscribe();

    expect(error).toMatchObject({ status, code });
    expect(getAccessToken()).toBe('still-valid');
    expect(listener).not.toHaveBeenCalled();
  });

  it('keeps the session when the refresh fails on the network', async () => {
    setSession('still-valid');
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    server.use(http.post('*/api/v1/auth/refresh', () => HttpResponse.error()));

    const error: unknown = await refreshAccessToken().catch((caught: unknown) => caught);
    unsubscribe();

    expect(error).toBeInstanceOf(TypeError);
    expect(getAccessToken()).toBe('still-valid');
    expect(listener).not.toHaveBeenCalled();
  });

  it('serializes across tabs through the Web Locks API when available', async () => {
    server.use(http.post('*/api/v1/auth/refresh', () => HttpResponse.json(buildRefreshResponse())));
    const request = vi.fn((_name: string, callback: () => Promise<string>) => callback());
    Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true });

    try {
      await expect(refreshAccessToken()).resolves.toBe('fresh-token');
      expect(request).toHaveBeenCalledWith('bomviz-refresh', expect.any(Function));
    } finally {
      Reflect.deleteProperty(navigator, 'locks');
    }
  });
});

describe('isSessionRejection', () => {
  const apiError = (status: number, code: string): ApiError =>
    new ApiError(status, { code, message: 'x', details: [] });

  it.each(['UNAUTHENTICATED', 'TOKEN_INVALID', 'TOKEN_EXPIRED', 'TOKEN_REUSED'])(
    'is true for 401 %s',
    (code) => {
      expect(isSessionRejection(apiError(401, code))).toBe(true);
    },
  );

  it('is false for other statuses, other codes and non-API errors', () => {
    expect(isSessionRejection(apiError(429, 'RATE_LIMITED'))).toBe(false);
    expect(isSessionRejection(apiError(503, 'TOKEN_INVALID'))).toBe(false);
    expect(isSessionRejection(apiError(401, 'INVALID_CREDENTIALS'))).toBe(false);
    expect(isSessionRejection(new TypeError('Failed to fetch'))).toBe(false);
  });
});

describe('withRefreshLock', () => {
  it('runs the task directly when Web Locks is unavailable', async () => {
    await expect(withRefreshLock(() => Promise.resolve('done'))).resolves.toBe('done');
  });
});
