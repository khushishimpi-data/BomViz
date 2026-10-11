import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { writeLastWorkspaceId } from '@/features/auth/last-workspace';
import { buildMe, buildMembership, resetAuthState, signInAs } from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

import { routes } from './routes';

afterEach(resetAuthState);

describe('routes', () => {
  it('renders the home page inside the application shell', async () => {
    renderRoutes(routes, '/');

    expect(
      screen.getByRole('heading', { name: 'Manufacturing BOMs, made understandable' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'BOMViz' })).toHaveAttribute('href', '/');
    expect(await screen.findByText('All systems operational')).toBeInTheDocument();
  });

  it('shows the workflow steps in order', () => {
    renderRoutes(routes, '/');

    const steps = screen.getAllByRole('listitem').map((item) => item.textContent);
    expect(steps).toEqual(['Upload', 'Map', 'Validate', 'Preview', 'Import', 'View']);
  });

  it('renders a not-found page for unknown paths', () => {
    renderRoutes(routes, '/no/such/page');

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/');
  });

  it('offers sign-in and registration links to anonymous visitors', async () => {
    renderRoutes(routes, '/');

    expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(screen.getByRole('link', { name: 'Create account' })).toHaveAttribute(
      'href',
      '/register',
    );
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument();
  });
});

describe('home page when signed in', () => {
  const memberships = [
    buildMembership({ workspace_id: 'ws-1', workspace_name: 'Acme Pumps' }),
    buildMembership({ workspace_id: 'ws-2', workspace_name: 'Beta Valves' }),
  ];

  it('redirects to the last-used workspace when the user still belongs to it', async () => {
    writeLastWorkspaceId('ws-2');
    signInAs(buildMe({ memberships }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-2', name: 'Beta Valves' }));
    const { router } = renderRoutes(routes, '/');

    expect(await screen.findByRole('heading', { name: 'Beta Valves' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces/ws-2');
  });

  it('falls back to the first workspace when the last-used one is not a membership', async () => {
    writeLastWorkspaceId('ws-gone');
    signInAs(buildMe({ memberships }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-1', name: 'Acme Pumps' }));
    const { router } = renderRoutes(routes, '/');

    expect(await screen.findByRole('heading', { name: 'Acme Pumps' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces/ws-1');
  });

  it('goes to the workspace list when the user has no workspaces', async () => {
    signInAs(buildMe({ memberships: [] }));
    const { router } = renderRoutes(routes, '/');

    expect(await screen.findByRole('heading', { name: 'Workspaces' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces');
    expect(
      screen.queryByRole('heading', { name: 'Manufacturing BOMs, made understandable' }),
    ).not.toBeInTheDocument();
  });
});
