import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { LAST_WORKSPACE_KEY } from '@/features/auth/last-workspace';
import { buildMe, buildMembership, resetAuthState, signInAs } from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

afterEach(resetAuthState);

const memberships = [
  buildMembership({ workspace_id: 'ws-1', workspace_name: 'Acme Pumps' }),
  buildMembership({ workspace_id: 'ws-2', workspace_name: 'Beta Valves', role: 'VIEWER' }),
];

describe('WorkspaceSwitcher', () => {
  it('is not shown to anonymous visitors', async () => {
    renderRoutes(routes, '/');

    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Workspace' })).not.toBeInTheDocument();
  });

  it("lists the user's workspaces and selects the current one", async () => {
    signInAs(buildMe({ memberships }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-1', name: 'Acme Pumps' }));
    renderRoutes(routes, '/workspaces/ws-1');

    const switcher = await screen.findByRole('combobox', { name: 'Workspace' });
    expect(switcher).toHaveValue('ws-1');
    const names = within(switcher)
      .getAllByRole('option')
      .filter((option) => !option.hasAttribute('disabled'))
      .map((option) => option.textContent);
    expect(names).toEqual(['Acme Pumps', 'Beta Valves']);
  });

  it('navigates to the chosen workspace and remembers it as last used', async () => {
    signInAs(buildMe({ memberships }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-2', name: 'Beta Valves', role: 'VIEWER' }));
    const { router } = renderRoutes(routes, '/workspaces');
    const user = userEvent.setup();

    const switcher = await screen.findByRole('combobox', { name: 'Workspace' });
    expect(switcher).toHaveValue('');
    await user.selectOptions(switcher, 'Beta Valves');

    expect(await screen.findByRole('heading', { name: 'Beta Valves' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces/ws-2');
    expect(window.localStorage.getItem(LAST_WORKSPACE_KEY)).toBe('ws-2');
    expect(switcher).toHaveValue('ws-2');
  });
});
