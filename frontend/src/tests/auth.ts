import { http, HttpResponse, type HttpHandler } from 'msw';

import type { LoginResponse, MeResponse, Membership } from '@/features/auth/api';
import type { ApiErrorBody } from '@/lib/api-error';
import { clearSession } from '@/lib/auth-session';

import { server } from './server';

export const TEST_ACCESS_TOKEN = 'test-access-token';

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * A JWT-shaped access token whose payload carries `sub`. The signature is a placeholder: the
 * client never verifies tokens, it only reads `sub` to notice a user switch.
 */
export function buildAccessToken(sub: string): string {
  const header = base64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = base64Url(JSON.stringify({ sub, typ: 'access', aud: 'bomviz-api' }));
  return `${header}.${payload}.signature`;
}

export function buildMembership(overrides: Partial<Membership> = {}): Membership {
  return {
    workspace_id: 'ws-1',
    workspace_name: 'Acme Pumps',
    workspace_slug: 'acme-pumps',
    role: 'ADMIN',
    ...overrides,
  };
}

/** Defaults to a user with no workspaces, so landing redirects go to `/workspaces`. */
export function buildMe(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    id: 'user-1',
    email: 'ada@example.com',
    full_name: 'Ada Lovelace',
    memberships: [],
    ...overrides,
  };
}

export function buildLoginResponse(me: MeResponse = buildMe()): LoginResponse {
  const user = { id: me.id, email: me.email, full_name: me.full_name };
  return { access_token: TEST_ACCESS_TOKEN, token_type: 'bearer', expires_in: 900, user };
}

export function errorEnvelope(code: string, message: string): { error: ApiErrorBody } {
  return { error: { code, message, details: [], request_id: 'req-test' } };
}

/** `/auth/me` that only answers requests carrying the test access token. */
export function meHandler(me: MeResponse = buildMe()): HttpHandler {
  return http.get('*/api/v1/auth/me', ({ request }) => {
    if (request.headers.get('Authorization') !== `Bearer ${TEST_ACCESS_TOKEN}`) {
      return HttpResponse.json(errorEnvelope('UNAUTHENTICATED', 'Authentication required.'), {
        status: 401,
      });
    }
    return HttpResponse.json(me);
  });
}

/**
 * Start the test signed in: the refresh cookie is "valid" (bootstrap gets a token) and
 * `/auth/me` returns `me`. Call before rendering.
 */
export function signInAs(me: MeResponse = buildMe()): MeResponse {
  server.use(
    http.post('*/api/v1/auth/refresh', () =>
      HttpResponse.json({ access_token: TEST_ACCESS_TOKEN, token_type: 'bearer', expires_in: 900 }),
    ),
    meHandler(me),
  );
  return me;
}

/** Reset module-level session state and stored preferences between tests. */
export function resetAuthState(): void {
  clearSession();
  window.localStorage.clear();
}
