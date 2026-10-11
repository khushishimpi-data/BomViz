import { screen } from '@testing-library/react';
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

afterEach(resetAuthState);

interface FormValues {
  fullName: string;
  email: string;
  password: string;
}

async function fillAndSubmit({ fullName, email, password }: FormValues): Promise<void> {
  const user = userEvent.setup();
  if (fullName) await user.type(await screen.findByLabelText('Full name'), fullName);
  if (email) await user.type(screen.getByLabelText('Email'), email);
  if (password) await user.type(screen.getByLabelText('Password'), password);
  await user.click(screen.getByRole('button', { name: 'Create account' }));
}

/** Records register/login calls so tests can assert what was (not) sent. */
function authEndpoints() {
  const calls = { register: [] as unknown[], login: [] as unknown[] };
  const handlers = [
    http.post('*/api/v1/auth/register', async ({ request }) => {
      calls.register.push(await request.json());
      return HttpResponse.json(
        { id: 'user-1', email: 'ada@example.com', full_name: 'Ada Lovelace' },
        { status: 201 },
      );
    }),
    http.post('*/api/v1/auth/login', async ({ request }) => {
      calls.login.push(await request.json());
      return HttpResponse.json(buildLoginResponse());
    }),
    meHandler(buildMe()),
  ];
  return { calls, handlers };
}

describe('RegisterPage', () => {
  it('rejects a short password without contacting the server', async () => {
    const endpoints = authEndpoints();
    server.use(...endpoints.handlers);
    renderRoutes(routes, '/register');

    await fillAndSubmit({ fullName: 'Ada Lovelace', email: 'ada@example.com', password: 'short' });

    expect(screen.getByText('Use at least 12 characters for your password.')).toBeInTheDocument();
    const password = screen.getByLabelText('Password');
    expect(password).toHaveAttribute('aria-invalid', 'true');
    expect(password).toHaveFocus();
    expect(screen.getByLabelText('Full name')).not.toHaveAttribute('aria-invalid');
    expect(endpoints.calls.register).toHaveLength(0);
  });

  it('rejects a password equal to the email and an empty name', async () => {
    const endpoints = authEndpoints();
    server.use(...endpoints.handlers);
    renderRoutes(routes, '/register');

    await fillAndSubmit({
      fullName: '',
      email: 'ada.lovelace@example.com',
      password: 'ada.lovelace@example.com',
    });

    expect(screen.getByText('Enter your full name.')).toBeInTheDocument();
    expect(
      screen.getByText('Choose a password that is different from your email address.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Full name')).toHaveFocus();
    expect(endpoints.calls.register).toHaveLength(0);
  });

  it('registers, signs in with the same credentials and lands on the workspaces page', async () => {
    const endpoints = authEndpoints();
    server.use(...endpoints.handlers);
    const { router } = renderRoutes(routes, '/register');

    await fillAndSubmit({
      fullName: '  Ada Lovelace ',
      email: 'ada@example.com',
      password: 'correct horse battery',
    });

    expect(await screen.findByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces');
    expect(endpoints.calls.register).toEqual([
      { full_name: 'Ada Lovelace', email: 'ada@example.com', password: 'correct horse battery' },
    ]);
    expect(endpoints.calls.login).toEqual([
      { email: 'ada@example.com', password: 'correct horse battery' },
    ]);
    expect(getAccessToken()).toBe(TEST_ACCESS_TOKEN);
  });

  it('shows the server message when the email is taken', async () => {
    server.use(
      http.post('*/api/v1/auth/register', () =>
        HttpResponse.json(errorEnvelope('EMAIL_TAKEN', 'An account with this email exists.'), {
          status: 409,
        }),
      ),
    );
    const { router } = renderRoutes(routes, '/register');

    await fillAndSubmit({
      fullName: 'Ada Lovelace',
      email: 'ada@example.com',
      password: 'correct horse battery',
    });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'An account with this email exists.',
    );
    expect(router.state.location.pathname).toBe('/register');
    expect(getAccessToken()).toBeNull();
  });
});
