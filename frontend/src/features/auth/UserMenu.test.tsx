import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { getAccessToken } from '@/lib/auth-session';
import { buildMe, errorEnvelope, resetAuthState, signInAs } from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';

afterEach(resetAuthState);

describe('UserMenu', () => {
  it('shows the signed-in user instead of the sign-in links', async () => {
    signInAs(buildMe({ full_name: 'Grace Hopper' }));
    renderRoutes(routes, '/workspaces');

    expect(await screen.findByText('Grace Hopper')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Sign in' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Create account' })).not.toBeInTheDocument();
  });

  it('signs out: calls /auth/logout, clears the session and returns home', async () => {
    signInAs(buildMe());
    let logoutCalls = 0;
    server.use(
      http.post('*/api/v1/auth/logout', () => {
        logoutCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { router } = renderRoutes(routes, '/workspaces');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Sign out' }));

    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeInTheDocument();
    expect(logoutCalls).toBe(1);
    expect(getAccessToken()).toBeNull();
    expect(router.state.location.pathname).toBe('/');
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument();
  });

  it('still clears the local session when the logout request fails', async () => {
    signInAs(buildMe());
    server.use(
      http.post('*/api/v1/auth/logout', () =>
        HttpResponse.json(errorEnvelope('INTERNAL_ERROR', 'Something broke.'), { status: 500 }),
      ),
    );
    renderRoutes(routes, '/workspaces');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Sign out' }));

    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });
});
