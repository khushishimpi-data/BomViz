import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import type { MeResponse } from '@/features/auth/api';
import { LAST_WORKSPACE_KEY } from '@/features/auth/last-workspace';
import {
  buildMe,
  buildMembership,
  errorEnvelope,
  resetAuthState,
  signInAs,
  TEST_ACCESS_TOKEN,
} from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildPage, buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

afterEach(resetAuthState);

function createEndpoint(respond: (body: unknown) => Response) {
  const bodies: unknown[] = [];
  return {
    bodies,
    handler: http.post('*/api/v1/workspaces', async ({ request }) => {
      const body: unknown = await request.json();
      bodies.push(body);
      return respond(body);
    }),
  };
}

describe('WorkspacesPage', () => {
  it("lists the caller's workspaces with their role", async () => {
    signInAs(buildMe());
    server.use(
      http.get('*/api/v1/workspaces', () =>
        HttpResponse.json(
          buildPage([
            buildWorkspace({ id: 'ws-1', name: 'Acme Pumps', role: 'ADMIN' }),
            buildWorkspace({ id: 'ws-2', name: 'Beta Valves', slug: 'beta', role: 'VIEWER' }),
          ]),
        ),
      ),
    );
    renderRoutes(routes, '/workspaces');

    const link = await screen.findByRole('link', { name: 'Beta Valves' });
    expect(link).toHaveAttribute('href', '/workspaces/ws-2');
    expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(2);
    expect(link.closest('li')).toHaveTextContent('Viewer');
    expect(link.closest('li')).not.toHaveTextContent('Admin');
    expect(screen.queryByText(/not a member of any workspace/)).not.toBeInTheDocument();
  });

  it('explains the empty state', async () => {
    signInAs(buildMe());
    renderRoutes(routes, '/workspaces');

    expect(await screen.findByText(/not a member of any workspace yet/)).toBeInTheDocument();
  });

  it('creates a workspace, reloads the profile and opens the new workspace', async () => {
    const created = buildWorkspace({ id: 'ws-new', name: 'Gamma Gears', slug: 'gamma-gears' });
    // The profile gains the membership once the workspace exists.
    let me: MeResponse = buildMe();
    let meCalls = 0;
    signInAs(me);
    const endpoint = createEndpoint(() => {
      me = buildMe({
        memberships: [buildMembership({ workspace_id: 'ws-new', workspace_name: 'Gamma Gears' })],
      });
      return HttpResponse.json(created, { status: 201 });
    });
    server.use(
      endpoint.handler,
      http.get('*/api/v1/auth/me', ({ request }) => {
        meCalls += 1;
        expect(request.headers.get('Authorization')).toBe(`Bearer ${TEST_ACCESS_TOKEN}`);
        return HttpResponse.json(me);
      }),
    );
    installWorkspaceApi(created);
    const { router } = renderRoutes(routes, '/workspaces');
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText('Name'), '  Gamma Gears ');
    await user.click(screen.getByRole('button', { name: 'Create workspace' }));

    expect(await screen.findByRole('heading', { name: 'Gamma Gears' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces/ws-new');
    // A blank slug is omitted so the server derives one from the name.
    expect(endpoint.bodies).toEqual([{ name: 'Gamma Gears' }]);
    expect(meCalls).toBe(2);
    expect(window.localStorage.getItem(LAST_WORKSPACE_KEY)).toBe('ws-new');
    expect(screen.getByRole('combobox', { name: 'Workspace' })).toHaveValue('ws-new');
  });

  it('rejects an invalid slug without contacting the server', async () => {
    signInAs(buildMe());
    const endpoint = createEndpoint(() => HttpResponse.json(buildWorkspace(), { status: 201 }));
    server.use(endpoint.handler);
    renderRoutes(routes, '/workspaces');
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText('Name'), 'Acme');
    await user.type(screen.getByLabelText('Slug (optional)'), 'Bad Slug');
    await user.click(screen.getByRole('button', { name: 'Create workspace' }));

    const slug = screen.getByLabelText('Slug (optional)');
    expect(slug).toHaveAttribute('aria-invalid', 'true');
    expect(slug).toHaveAccessibleDescription(/lowercase letters and digits/);
    expect(slug).toHaveFocus();
    expect(endpoint.bodies).toEqual([]);
  });

  it('shows SLUG_TAKEN inline and stays on the page', async () => {
    signInAs(buildMe());
    const endpoint = createEndpoint(() =>
      HttpResponse.json(errorEnvelope('SLUG_TAKEN', 'Slug already in use.'), { status: 409 }),
    );
    server.use(endpoint.handler);
    const { router } = renderRoutes(routes, '/workspaces');
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText('Name'), 'Acme');
    await user.type(screen.getByLabelText('Slug (optional)'), 'acme');
    await user.click(screen.getByRole('button', { name: 'Create workspace' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That slug is already used by another workspace.',
    );
    expect(endpoint.bodies).toEqual([{ name: 'Acme', slug: 'acme' }]);
    expect(router.state.location.pathname).toBe('/workspaces');
  });
});
