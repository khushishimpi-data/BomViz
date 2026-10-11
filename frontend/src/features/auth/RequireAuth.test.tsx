import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { buildMe, resetAuthState, signInAs } from '@/tests/auth';
import { renderRoutes } from '@/tests/render';

afterEach(resetAuthState);

describe('RequireAuth', () => {
  it('redirects anonymous users to sign in with the full return path as next', async () => {
    const { router } = renderRoutes(routes, '/workspaces/ws-1/products?q=pump');

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/login');
    expect(new URLSearchParams(router.state.location.search).get('next')).toBe(
      '/workspaces/ws-1/products?q=pump',
    );
    expect(screen.queryByRole('heading', { name: 'Workspace' })).not.toBeInTheDocument();
  });

  it('shows a loading state while the session is being restored', async () => {
    renderRoutes(routes, '/workspaces');

    // The header's service indicator is also a status region, so match on the text.
    expect(screen.getByText('Checking your session…')).toHaveAttribute('role', 'status');
    expect(screen.queryByRole('heading', { name: 'Workspaces' })).not.toBeInTheDocument();
    // Let the bootstrap settle: the refresh is single-flight across the module, so an
    // in-flight one would otherwise be shared by the next test.
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('renders the protected page for a signed-in user', async () => {
    signInAs(buildMe());
    const { router } = renderRoutes(routes, '/workspaces');

    expect(await screen.findByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces');
  });
});
