import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { apiRequest } from '@/lib/api-client';
import { clearSession, getAccessToken, refreshAccessToken } from '@/lib/auth-session';
import {
  buildAccessToken,
  buildMe,
  buildMembership,
  errorEnvelope,
  resetAuthState,
  signInAs,
  TEST_ACCESS_TOKEN,
} from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';

import type { MeResponse } from './api';
import { AuthProvider } from './AuthProvider';
import { useAuth } from './useAuth';

afterEach(resetAuthState);

/** Renders what a consumer of the context sees, one fact per line. */
function AuthProbe() {
  const { status, user, memberships, logout } = useAuth();
  return (
    <>
      <p>Status: {status}</p>
      <p>User: {user?.full_name ?? 'none'}</p>
      <p>Workspaces: {memberships.map((m) => m.workspace_name).join(', ') || 'none'}</p>
      <button type="button" onClick={() => void logout()}>
        Sign out
      </button>
    </>
  );
}

function renderProvider(): void {
  render(
    <AuthProvider>
      <AuthProbe />
    </AuthProvider>,
  );
}

/** `/auth/me` keyed by bearer token, so each JWT-shaped token resolves to its own user. */
function meByToken(users: Readonly<Record<string, MeResponse>>) {
  return http.get('*/api/v1/auth/me', ({ request }) => {
    const token = request.headers.get('Authorization')?.replace('Bearer ', '') ?? '';
    const me = users[token];
    if (me === undefined) {
      return HttpResponse.json(errorEnvelope('UNAUTHENTICATED', 'Authentication required.'), {
        status: 401,
      });
    }
    return HttpResponse.json(me);
  });
}

function refreshReturning(token: string) {
  return http.post('*/api/v1/auth/refresh', () =>
    HttpResponse.json({ access_token: token, token_type: 'bearer', expires_in: 900 }),
  );
}

describe('AuthProvider', () => {
  it('restores the session from the refresh cookie and loads the profile', async () => {
    signInAs(buildMe({ memberships: [buildMembership({ workspace_name: 'Acme Pumps' })] }));
    renderProvider();

    expect(screen.getByText('Status: loading')).toBeInTheDocument();
    expect(await screen.findByText('Status: authenticated')).toBeInTheDocument();
    expect(screen.getByText('User: Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('Workspaces: Acme Pumps')).toBeInTheDocument();
    expect(getAccessToken()).toBe(TEST_ACCESS_TOKEN);
  });

  it('is anonymous without calling /auth/me when the refresh is refused', async () => {
    let meCalls = 0;
    server.use(
      http.get('*/api/v1/auth/me', () => {
        meCalls += 1;
        return HttpResponse.json(buildMe());
      }),
    );
    renderProvider();

    expect(await screen.findByText('Status: anonymous')).toBeInTheDocument();
    expect(screen.getByText('User: none')).toBeInTheDocument();
    expect(meCalls).toBe(0);
  });

  it.each([
    [
      '503',
      () => HttpResponse.json(errorEnvelope('SERVICE_UNAVAILABLE', 'Down.'), { status: 503 }),
    ],
    ['a network error', () => HttpResponse.error()],
  ])('starts anonymous when the startup refresh fails with %s', async (_label, respond) => {
    server.use(http.post('*/api/v1/auth/refresh', respond));
    renderProvider();

    expect(await screen.findByText('Status: anonymous')).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });

  it('drops the token when the profile cannot be loaded', async () => {
    signInAs();
    server.use(
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json(errorEnvelope('UNAUTHENTICATED', 'Authentication required.'), {
          status: 401,
        }),
      ),
    );
    renderProvider();

    expect(await screen.findByText('Status: anonymous')).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });

  it('becomes anonymous when the session is cleared elsewhere (failed refresh mid-use)', async () => {
    signInAs();
    renderProvider();
    expect(await screen.findByText('Status: authenticated')).toBeInTheDocument();

    act(() => {
      clearSession();
    });

    expect(screen.getByText('Status: anonymous')).toBeInTheDocument();
    expect(screen.getByText('User: none')).toBeInTheDocument();
  });

  it('stays signed in when a mid-use refresh fails transiently', async () => {
    signInAs();
    renderProvider();
    expect(await screen.findByText('Status: authenticated')).toBeInTheDocument();
    server.use(
      http.get('*/api/v1/things', () =>
        HttpResponse.json(errorEnvelope('TOKEN_EXPIRED', 'Expired.'), { status: 401 }),
      ),
      http.post('*/api/v1/auth/refresh', () =>
        HttpResponse.json(errorEnvelope('SERVICE_UNAVAILABLE', 'Down.'), { status: 503 }),
      ),
    );

    const error: unknown = await act(() =>
      apiRequest('GET', '/api/v1/things').catch((caught: unknown) => caught),
    );

    expect(error).toMatchObject({ status: 503 });
    expect(screen.getByText('Status: authenticated')).toBeInTheDocument();
    expect(screen.getByText('User: Ada Lovelace')).toBeInTheDocument();
    expect(getAccessToken()).toBe(TEST_ACCESS_TOKEN);
  });

  it('reloads the profile when the token switches to another user', async () => {
    const tokenA = buildAccessToken('user-a');
    const tokenB = buildAccessToken('user-b');
    server.use(
      refreshReturning(tokenA),
      meByToken({
        [tokenA]: buildMe({ id: 'user-a', full_name: 'User A' }),
        [tokenB]: buildMe({ id: 'user-b', full_name: 'User B' }),
      }),
    );
    renderProvider();
    expect(await screen.findByText('User: User A')).toBeInTheDocument();

    // Another tab signed in as B; this tab's next refresh picks up B's cookie.
    server.use(refreshReturning(tokenB));
    await act(() => refreshAccessToken());

    expect(await screen.findByText('User: User B')).toBeInTheDocument();
    expect(screen.queryByText('User: User A')).not.toBeInTheDocument();
    expect(screen.getByText('Status: authenticated')).toBeInTheDocument();
  });

  it('sends the logout request while holding the cross-tab refresh lock', async () => {
    signInAs();
    let lockHeld = false;
    const lockNames: string[] = [];
    const request = async (name: string, task: () => Promise<unknown>): Promise<unknown> => {
      lockNames.push(name);
      lockHeld = true;
      try {
        return await task();
      } finally {
        lockHeld = false;
      }
    };
    const heldDuringLogout: boolean[] = [];
    server.use(
      http.post('*/api/v1/auth/logout', () => {
        heldDuringLogout.push(lockHeld);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true });

    try {
      renderProvider();
      expect(await screen.findByText('Status: authenticated')).toBeInTheDocument();
      // The startup refresh also took the lock; only the logout's acquisition matters here.
      lockNames.length = 0;

      await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }));

      expect(await screen.findByText('Status: anonymous')).toBeInTheDocument();
      expect(heldDuringLogout).toEqual([true]);
      expect(lockNames).toEqual(['bomviz-refresh']);
      expect(getAccessToken()).toBeNull();
    } finally {
      Reflect.deleteProperty(navigator, 'locks');
    }
  });
});

describe('AuthProvider within the app (integration)', () => {
  it('keeps the user on the page with an error when a refresh is rate limited', async () => {
    let refreshCalls = 0;
    server.use(
      http.post('*/api/v1/auth/refresh', () => {
        refreshCalls += 1;
        if (refreshCalls === 1) {
          return HttpResponse.json({
            access_token: TEST_ACCESS_TOKEN,
            token_type: 'bearer',
            expires_in: 900,
          });
        }
        return HttpResponse.json(errorEnvelope('RATE_LIMITED', 'Too many requests.'), {
          status: 429,
        });
      }),
      http.get('*/api/v1/auth/me', () => HttpResponse.json(buildMe())),
      http.get('*/api/v1/workspaces', () =>
        HttpResponse.json(errorEnvelope('TOKEN_EXPIRED', 'Expired.'), { status: 401 }),
      ),
    );
    const { router } = renderRoutes(routes, '/workspaces');

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(refreshCalls).toBe(2);
    expect(router.state.location.pathname).toBe('/workspaces');
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(getAccessToken()).toBe(TEST_ACCESS_TOKEN);
  });
});
