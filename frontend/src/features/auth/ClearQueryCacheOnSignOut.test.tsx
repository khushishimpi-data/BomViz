import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { clearSession, refreshAccessToken } from '@/lib/auth-session';
import {
  buildAccessToken,
  buildLoginResponse,
  buildMe,
  errorEnvelope,
  resetAuthState,
  TEST_ACCESS_TOKEN,
} from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildPage, buildWorkspace } from '@/tests/workspaces';

import type { MeResponse } from './api';

afterEach(resetAuthState);

const userA = buildMe({ id: 'user-a', email: 'a@example.com', full_name: 'User A' });
const userB = buildMe({ id: 'user-b', email: 'b@example.com', full_name: 'User B' });
const workspacesOf: Record<string, string> = { 'user-a': 'A Secret Works', 'user-b': 'B Works' };

/**
 * A backend where the bearer token belongs to whoever signed in last. Requests without the
 * token are rejected, as the real API does, so a cleared cache cannot be refilled anonymously.
 */
function installBackend(initial: MeResponse): void {
  let current = initial;
  const unauthenticated = (): Response =>
    HttpResponse.json(errorEnvelope('UNAUTHENTICATED', 'Authentication required.'), {
      status: 401,
    });
  const hasToken = (request: Request): boolean =>
    request.headers.get('Authorization') === `Bearer ${TEST_ACCESS_TOKEN}`;

  server.use(
    http.post('*/api/v1/auth/refresh', () =>
      HttpResponse.json({ access_token: TEST_ACCESS_TOKEN, token_type: 'bearer', expires_in: 900 }),
    ),
    http.post('*/api/v1/auth/login', () => {
      current = userB;
      return HttpResponse.json(buildLoginResponse(userB));
    }),
    http.post('*/api/v1/auth/logout', () => new HttpResponse(null, { status: 204 })),
    http.get('*/api/v1/auth/me', ({ request }) =>
      hasToken(request) ? HttpResponse.json(current) : unauthenticated(),
    ),
    http.get('*/api/v1/workspaces', ({ request }) => {
      if (!hasToken(request)) return unauthenticated();
      const name = workspacesOf[current.id] ?? 'Unknown';
      return HttpResponse.json(buildPage([buildWorkspace({ id: `ws-${current.id}`, name })]));
    }),
  );
}

async function signInAsB(): Promise<void> {
  const user = userEvent.setup();
  // Paste rather than type: each test already signs in twice through the UI.
  await user.click(await screen.findByLabelText('Email'));
  await user.paste(userB.email);
  await user.click(screen.getByLabelText('Password'));
  await user.paste('correct horse battery');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
}

describe('ClearQueryCacheOnSignOut', () => {
  beforeEach(() => {
    installBackend(userA);
  });

  it("drops user A's cached data on sign-out, so user B never sees it", async () => {
    const user = userEvent.setup();
    renderRoutes(routes, '/workspaces');
    expect(await screen.findByRole('link', { name: 'A Secret Works' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await user.click(await screen.findByRole('link', { name: 'Sign in' }));
    await signInAsB();

    // Without the cache reset the fresh (30 s staleTime) list for A would render instantly.
    expect(await screen.findByRole('link', { name: 'B Works' })).toBeInTheDocument();
    expect(screen.queryByText('A Secret Works')).not.toBeInTheDocument();
  });

  it('also drops cached data when the session is cleared after a failed refresh', async () => {
    const { router } = renderRoutes(routes, '/workspaces');
    expect(await screen.findByRole('link', { name: 'A Secret Works' })).toBeInTheDocument();

    act(() => {
      clearSession();
    });
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    await signInAsB();

    expect(await screen.findByRole('link', { name: 'B Works' })).toBeInTheDocument();
    expect(screen.queryByText('A Secret Works')).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces');
  });
  it('drops cached data when the token switches to another user without signing out', async () => {
    const tokenA = buildAccessToken('user-a');
    const tokenB = buildAccessToken('user-b');
    const users: Record<string, MeResponse> = { [tokenA]: userA, [tokenB]: userB };
    let refreshToken = tokenA;
    server.use(
      http.post('*/api/v1/auth/refresh', () =>
        HttpResponse.json({ access_token: refreshToken, token_type: 'bearer', expires_in: 900 }),
      ),
      http.get('*/api/v1/auth/me', ({ request }) => {
        const me = users[request.headers.get('Authorization')?.slice('Bearer '.length) ?? ''];
        return me === undefined
          ? HttpResponse.json(errorEnvelope('UNAUTHENTICATED', 'Authentication required.'), {
              status: 401,
            })
          : HttpResponse.json(me);
      }),
      http.get('*/api/v1/workspaces', ({ request }) => {
        const me = users[request.headers.get('Authorization')?.slice('Bearer '.length) ?? ''];
        const name = me === undefined ? 'Unknown' : (workspacesOf[me.id] ?? 'Unknown');
        return HttpResponse.json(buildPage([buildWorkspace({ id: `ws-${name}`, name })]));
      }),
    );
    renderRoutes(routes, '/workspaces');
    expect(await screen.findByRole('link', { name: 'A Secret Works' })).toBeInTheDocument();
    expect(screen.getByText('User A')).toBeInTheDocument();

    // Another tab signed in as B; this tab's next refresh picks up B's cookie.
    refreshToken = tokenB;
    await act(() => refreshAccessToken());

    expect(await screen.findByRole('link', { name: 'B Works' })).toBeInTheDocument();
    expect(await screen.findByText('User B')).toBeInTheDocument();
    expect(screen.queryByText('A Secret Works')).not.toBeInTheDocument();
    expect(screen.queryByText('User A')).not.toBeInTheDocument();
  });
});
