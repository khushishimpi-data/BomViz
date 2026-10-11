import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { getAccessToken } from '@/lib/auth-session';
import {
  buildLoginResponse,
  buildMe,
  errorEnvelope,
  meHandler,
  resetAuthState,
  TEST_ACCESS_TOKEN,
} from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

afterEach(resetAuthState);

/** Login endpoint that accepts exactly one email/password pair and records every attempt. */
function loginEndpoint(email = 'ada@example.com', password = 'correct horse battery') {
  const attempts: unknown[] = [];
  const handler = http.post('*/api/v1/auth/login', async ({ request }) => {
    const body = (await request.json()) as { email: string; password: string };
    attempts.push(body);
    if (body.email === email && body.password === password) {
      return HttpResponse.json(buildLoginResponse());
    }
    return HttpResponse.json(errorEnvelope('INVALID_CREDENTIALS', 'Invalid email or password.'), {
      status: 401,
    });
  });
  return { attempts, handler };
}

async function submitLogin(email: string, password: string): Promise<void> {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText('Email'), email);
  await user.type(screen.getByLabelText('Password'), password);
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
}

describe('LoginPage', () => {
  it('signs in and navigates to the requested next path', async () => {
    const login = loginEndpoint();
    server.use(login.handler, meHandler(buildMe()));
    installWorkspaceApi(buildWorkspace({ id: 'ws-9', name: 'Gamma Gears' }));
    const { router } = renderRoutes(routes, '/login?next=%2Fworkspaces%2Fws-9%3Ftab%3Dmembers');

    await submitLogin('ada@example.com', 'correct horse battery');

    expect(await screen.findByRole('heading', { name: 'Gamma Gears' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces/ws-9');
    expect(router.state.location.search).toBe('?tab=members');
    expect(getAccessToken()).toBe(TEST_ACCESS_TOKEN);
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Sign in' })).not.toBeInTheDocument();
  });

  it('ignores a protocol-relative next and stays on this origin', async () => {
    server.use(loginEndpoint().handler, meHandler(buildMe()));
    const { router } = renderRoutes(routes, '/login?next=%2F%2Fevil.com');

    await submitLogin('ada@example.com', 'correct horse battery');

    // Default destination is `/`, which sends a user without workspaces to the list.
    expect(await screen.findByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces');
    expect(router.state.location.pathname).not.toContain('evil.com');
  });

  it('shows the server message for bad credentials and stays signed out', async () => {
    server.use(loginEndpoint().handler);
    const { router } = renderRoutes(routes, '/login');

    await submitLogin('ada@example.com', 'wrong password!!');

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.');
    expect(router.state.location.pathname).toBe('/login');
    expect(getAccessToken()).toBeNull();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled();
  });

  it('shows the throttle message with the Retry-After wait', async () => {
    server.use(
      http.post('*/api/v1/auth/login', () =>
        HttpResponse.json(
          errorEnvelope('LOGIN_THROTTLED', 'Too many sign-in attempts. Please wait and try again.'),
          { status: 429, headers: { 'Retry-After': '60' } },
        ),
      ),
    );
    renderRoutes(routes, '/login');

    await submitLogin('ada@example.com', 'correct horse battery');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Too many sign-in attempts. Please wait and try again. Try again in 60 seconds.',
    );
  });

  it('shows only the server message when a 429 has no Retry-After', async () => {
    server.use(
      http.post('*/api/v1/auth/login', () =>
        HttpResponse.json(errorEnvelope('RATE_LIMITED', 'Too many requests.'), { status: 429 }),
      ),
    );
    renderRoutes(routes, '/login');

    await submitLogin('ada@example.com', 'correct horse battery');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Too many requests.');
    expect(alert).not.toHaveTextContent('Try again in');
  });

  it('validates required fields before sending anything', async () => {
    const login = loginEndpoint();
    server.use(login.handler);
    renderRoutes(routes, '/login');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    expect(screen.getByText('Enter your email address.')).toBeInTheDocument();
    expect(screen.getByText('Enter your password.')).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Email')).toHaveFocus();
    expect(login.attempts).toHaveLength(0);
  });

  it('disables the submit button while the request is pending', async () => {
    let release: () => void = () => undefined;
    server.use(
      http.post(
        '*/api/v1/auth/login',
        () =>
          new Promise<Response>((resolve) => {
            release = () => {
              resolve(HttpResponse.json(buildLoginResponse()));
            };
          }),
      ),
      meHandler(buildMe()),
    );
    renderRoutes(routes, '/login');

    await submitLogin('ada@example.com', 'correct horse battery');

    expect(await screen.findByRole('button', { name: 'Signing in…' })).toBeDisabled();
    release();
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Signing in…' })).not.toBeInTheDocument();
    });
  });
});
