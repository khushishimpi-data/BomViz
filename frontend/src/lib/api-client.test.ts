import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/tests/server';

import { ApiError, apiRequest, getJson } from './api-client';
import { clearSession, getAccessToken, setSession, subscribe } from './auth-session';

describe('getJson', () => {
  it('returns the parsed body for a successful response', async () => {
    server.use(http.get('*/api/v1/things', () => HttpResponse.json({ count: 3 })));

    await expect(getJson('http://localhost/api/v1/things')).resolves.toEqual({ count: 3 });
  });

  it('throws ApiError carrying the envelope code, message and request id', async () => {
    server.use(
      http.get('*/api/v1/boms/1', () =>
        HttpResponse.json(
          {
            error: {
              code: 'VALIDATION_FAILED',
              message: 'BOM contains blocking validation errors.',
              details: [{ row_number: 4 }],
              request_id: 'req-1',
            },
          },
          { status: 409 },
        ),
      ),
    );

    const error: unknown = await getJson('http://localhost/api/v1/boms/1').catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: 'VALIDATION_FAILED',
      message: 'BOM contains blocking validation errors.',
      details: [{ row_number: 4 }],
      requestId: 'req-1',
    });
  });

  it('uses a generic message when the body is not an error envelope', async () => {
    server.use(
      http.get(
        '*/api/v1/broken',
        () =>
          new HttpResponse('<html>Bad Gateway</html>', {
            status: 502,
            headers: { 'X-Request-ID': 'req-2' },
          }),
      ),
    );

    const error: unknown = await getJson('http://localhost/api/v1/broken').catch(
      (caught: unknown) => caught,
    );

    expect(error).toMatchObject({ status: 502, code: 'HTTP_ERROR', requestId: 'req-2' });
    expect((error as ApiError).message).not.toContain('Bad Gateway');
  });
});

function errorEnvelope(code: string, message = 'Request failed.') {
  return { error: { code, message, details: [], request_id: 'req-1' } };
}

function buildRefreshResponse(accessToken = 'fresh-token') {
  return { access_token: accessToken, token_type: 'bearer', expires_in: 900 };
}

/** A protected endpoint that only accepts `validToken`; anything else is an expired token. */
function protectedThing(validToken: string, onRequest?: (authorization: string | null) => void) {
  return http.get('*/api/v1/things', ({ request }) => {
    const authorization = request.headers.get('Authorization');
    onRequest?.(authorization);
    if (authorization === `Bearer ${validToken}`) return HttpResponse.json({ ok: true });
    return HttpResponse.json(errorEnvelope('TOKEN_EXPIRED'), { status: 401 });
  });
}

/** Refresh endpoint that counts calls and hands out `validToken`. */
function countingRefresh(validToken: string) {
  const counter = { calls: 0 };
  const handler = http.post('*/api/v1/auth/refresh', () => {
    counter.calls += 1;
    return HttpResponse.json(buildRefreshResponse(validToken));
  });
  return { counter, handler };
}

describe('apiRequest', () => {
  afterEach(() => {
    clearSession();
  });

  it('attaches the bearer token and sends the body as JSON', async () => {
    setSession('token-1');
    let seen: { authorization: string | null; contentType: string | null; body: unknown } | null =
      null;
    server.use(
      http.post('*/api/v1/things', async ({ request }) => {
        seen = {
          authorization: request.headers.get('Authorization'),
          contentType: request.headers.get('Content-Type'),
          body: await request.json(),
        };
        return HttpResponse.json({ id: 'thing-1' }, { status: 201 });
      }),
    );

    await expect(apiRequest('POST', '/api/v1/things', { body: { name: 'Pump' } })).resolves.toEqual(
      { id: 'thing-1' },
    );
    expect(seen).toEqual({
      authorization: 'Bearer token-1',
      contentType: 'application/json',
      body: { name: 'Pump' },
    });
  });

  it('omits the Authorization header when there is no session', async () => {
    let authorization: string | null = 'unset';
    server.use(
      http.get('*/api/v1/public', ({ request }) => {
        authorization = request.headers.get('Authorization');
        return HttpResponse.json({ ok: true });
      }),
    );

    await apiRequest('GET', '/api/v1/public');

    expect(authorization).toBeNull();
  });

  it('resolves to undefined for a 204 response', async () => {
    server.use(http.delete('*/api/v1/things/1', () => new HttpResponse(null, { status: 204 })));

    await expect(apiRequest<undefined>('DELETE', '/api/v1/things/1')).resolves.toBeUndefined();
  });

  it('refreshes once and retries with the new token after TOKEN_EXPIRED', async () => {
    setSession('expired');
    const seen: (string | null)[] = [];
    const refresh = countingRefresh('fresh-token');
    server.use(
      protectedThing('fresh-token', (authorization) => seen.push(authorization)),
      refresh.handler,
    );

    await expect(apiRequest('GET', '/api/v1/things')).resolves.toEqual({ ok: true });

    expect(refresh.counter.calls).toBe(1);
    expect(seen).toEqual(['Bearer expired', 'Bearer fresh-token']);
    expect(getAccessToken()).toBe('fresh-token');
  });

  it('retries only once when the retried request is still unauthorized', async () => {
    setSession('expired');
    const refresh = countingRefresh('also-rejected');
    server.use(protectedThing('never-valid'), refresh.handler);

    const error: unknown = await apiRequest('GET', '/api/v1/things').catch(
      (caught: unknown) => caught,
    );

    expect(error).toMatchObject({ status: 401, code: 'TOKEN_EXPIRED' });
    expect(refresh.counter.calls).toBe(1);
  });

  it('clears the session and throws the ApiError when the refresh is rejected', async () => {
    setSession('expired');
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    server.use(
      protectedThing('fresh-token'),
      http.post('*/api/v1/auth/refresh', () =>
        HttpResponse.json(errorEnvelope('TOKEN_INVALID'), { status: 401 }),
      ),
    );

    const error: unknown = await apiRequest('GET', '/api/v1/things').catch(
      (caught: unknown) => caught,
    );
    unsubscribe();

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: 'TOKEN_EXPIRED' });
    expect(getAccessToken()).toBeNull();
    expect(listener).toHaveBeenCalledWith({ token: null, userChanged: false });
  });

  it.each([
    ['503', () => HttpResponse.json(errorEnvelope('SERVICE_UNAVAILABLE'), { status: 503 })],
    ['429', () => HttpResponse.json(errorEnvelope('RATE_LIMITED'), { status: 429 })],
  ])(
    'keeps the session and throws the refresh error when the refresh fails with %s',
    async (status, respond) => {
      setSession('expired');
      const listener = vi.fn();
      const unsubscribe = subscribe(listener);
      server.use(protectedThing('fresh-token'), http.post('*/api/v1/auth/refresh', respond));

      const error: unknown = await apiRequest('GET', '/api/v1/things').catch(
        (caught: unknown) => caught,
      );
      unsubscribe();

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: Number(status) });
      expect(error).not.toMatchObject({ code: 'TOKEN_EXPIRED' });
      expect(getAccessToken()).toBe('expired');
      expect(listener).not.toHaveBeenCalled();
    },
  );

  it('keeps the session when the refresh fails on the network', async () => {
    setSession('expired');
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    server.use(
      protectedThing('fresh-token'),
      http.post('*/api/v1/auth/refresh', () => HttpResponse.error()),
    );

    const error: unknown = await apiRequest('GET', '/api/v1/things').catch(
      (caught: unknown) => caught,
    );
    unsubscribe();

    expect(error).toBeInstanceOf(TypeError);
    expect(getAccessToken()).toBe('expired');
    expect(listener).not.toHaveBeenCalled();
  });

  it('does not refresh for 401s that a new token cannot fix', async () => {
    const refresh = countingRefresh('fresh-token');
    server.use(
      http.post('*/api/v1/auth/login', () =>
        HttpResponse.json(errorEnvelope('INVALID_CREDENTIALS', 'Invalid email or password.'), {
          status: 401,
        }),
      ),
      refresh.handler,
    );

    const error: unknown = await apiRequest('POST', '/api/v1/auth/login', {
      body: { email: 'a@example.com', password: 'wrong-password' },
    }).catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      code: 'INVALID_CREDENTIALS',
      message: 'Invalid email or password.',
    });
    expect(refresh.counter.calls).toBe(0);
  });

  it('triggers a single refresh for concurrent 401s', async () => {
    setSession('expired');
    const refresh = countingRefresh('fresh-token');
    server.use(protectedThing('fresh-token'), refresh.handler);

    const results = await Promise.all([
      apiRequest('GET', '/api/v1/things'),
      apiRequest('GET', '/api/v1/things'),
      apiRequest('GET', '/api/v1/things'),
    ]);

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(refresh.counter.calls).toBe(1);
  });
});
