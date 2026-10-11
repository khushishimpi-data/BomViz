import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { LAST_WORKSPACE_KEY } from '@/features/auth/last-workspace';
import { buildMe, buildMembership, resetAuthState, signInAs } from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

afterEach(resetAuthState);

describe('WorkspaceOverviewPage', () => {
  it('shows the workspace with links to its products and members, and remembers it', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-1', name: 'Acme Pumps', role: 'ENGINEER' }));
    renderRoutes(routes, '/workspaces/ws-1');

    expect(await screen.findByRole('heading', { name: 'Acme Pumps' })).toBeInTheDocument();
    expect(screen.getByText('Your role: Engineer')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Products' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/products',
    );
    expect(screen.getByRole('link', { name: 'Members' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/members',
    );
    expect(window.localStorage.getItem(LAST_WORKSPACE_KEY)).toBe('ws-1');
  });

  it('shows a not-found state for a workspace the user cannot open, without remembering it', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-1' }));
    renderRoutes(routes, '/workspaces/ws-other');

    expect(await screen.findByRole('heading', { name: 'Workspace not found' })).toBeInTheDocument();
    // The header switcher has an "All workspaces" link too; this one is in the page body.
    expect(
      within(screen.getByRole('main')).getByRole('link', { name: 'All workspaces' }),
    ).toHaveAttribute('href', '/workspaces');
    expect(screen.queryByRole('link', { name: 'Members' })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(LAST_WORKSPACE_KEY)).toBeNull();
  });
});
