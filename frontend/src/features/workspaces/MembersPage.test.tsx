import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { buildMe, buildMembership, errorEnvelope, resetAuthState, signInAs } from '@/tests/auth';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildMember, buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

import type { Member } from './api';

afterEach(resetAuthState);

const MEMBERS_PATH = '/workspaces/ws-1/members';

const ada = buildMember({ id: 'mem-1', user_id: 'user-1', full_name: 'Ada Lovelace' });
const grace = buildMember({
  id: 'mem-2',
  user_id: 'user-2',
  email: 'grace@example.com',
  full_name: 'Grace Hopper',
  role: 'ENGINEER',
});
const linus = buildMember({
  id: 'mem-3',
  user_id: 'user-3',
  email: 'linus@example.com',
  full_name: 'Linus Torvalds',
  role: 'VIEWER',
});

/** Signed in as Ada (user-1) with the given role in ws-1, on the members page. */
function renderMembersPage(
  role: Member['role'] = 'ADMIN',
  members: readonly Member[] = [ada, grace, linus],
) {
  signInAs(buildMe({ memberships: [buildMembership({ role })] }));
  const api = installWorkspaceApi(buildWorkspace({ role }), members);
  const view = renderRoutes(routes, MEMBERS_PATH);
  return { api, ...view };
}

async function findTable(): Promise<HTMLElement> {
  return screen.findByRole('table', { name: 'Members of Acme Pumps' });
}

function rowFor(table: HTMLElement, name: string): HTMLElement {
  const header = within(table).getByRole('rowheader', { name: new RegExp(name) });
  const row = header.closest('tr');
  if (row === null) throw new Error(`No row for ${name}`);
  return row;
}

async function openRemoveDialog(name: string) {
  const user = userEvent.setup();
  await findTable();
  const trigger = screen.getByRole('button', { name: `Remove ${name}` });
  await user.click(trigger);
  const dialog = screen.getByRole('dialog', { name: 'Remove member?' });
  return { user, trigger, dialog };
}

describe('MembersPage as ADMIN', () => {
  it('lists members with role pickers, Remove buttons and the add form', async () => {
    renderMembersPage();

    const table = await findTable();
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(
      within(rowFor(table, 'Grace Hopper')).getByText('grace@example.com'),
    ).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Role for Grace Hopper' })).toHaveValue('ENGINEER');
    expect(screen.getByRole('button', { name: 'Remove Linus Torvalds' })).toBeEnabled();
    expect(screen.getByRole('form', { name: 'Add a member' })).toBeInTheDocument();
    expect(within(rowFor(table, 'Ada Lovelace')).getByText('(you)')).toBeInTheDocument();
  });

  it.each([
    ['USER_NOT_FOUND', 404, 'No BOMViz account uses that email.'],
    ['ALREADY_MEMBER', 409, 'That person is already a member of this workspace.'],
  ])('shows %s from the server next to the add form', async (code, status, message) => {
    renderMembersPage();
    const bodies: unknown[] = [];
    server.use(
      http.post('*/api/v1/workspaces/ws-1/members', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(errorEnvelope(code, 'Server says no.'), { status });
      }),
    );
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Add a member' });

    await user.type(within(form).getByLabelText('Email'), 'nobody@example.com');
    await user.selectOptions(within(form).getByLabelText('Role'), 'Engineer');
    await user.click(within(form).getByRole('button', { name: 'Add member' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent(message);
    expect(bodies).toEqual([{ email: 'nobody@example.com', role: 'ENGINEER' }]);
    expect(within(form).getByLabelText('Email')).toHaveValue('nobody@example.com');
  });

  it('adds a member and shows them in the table', async () => {
    const { api } = renderMembersPage('ADMIN', [ada]);
    const newcomer = buildMember({
      id: 'mem-9',
      user_id: 'user-9',
      email: 'new@example.com',
      full_name: 'New Person',
      role: 'VIEWER',
    });
    server.use(
      http.post('*/api/v1/workspaces/ws-1/members', () => {
        api.members = [...api.members, newcomer];
        return HttpResponse.json(newcomer, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Add a member' });

    await user.type(within(form).getByLabelText('Email'), 'new@example.com');
    await user.click(within(form).getByRole('button', { name: 'Add member' }));

    expect(await screen.findByRole('rowheader', { name: 'New Person' })).toBeInTheDocument();
    expect(within(form).getByRole('status')).toHaveTextContent('Added New Person as Viewer.');
    expect(within(form).getByLabelText('Email')).toHaveValue('');
  });

  it('changes a role with PATCH', async () => {
    const { api } = renderMembersPage();
    const user = userEvent.setup();
    await findTable();

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Role for Grace Hopper' }),
      'Procurement',
    );

    await waitFor(() => {
      expect(api.mutations).toEqual([
        {
          method: 'PATCH',
          path: '/api/v1/workspaces/ws-1/members/mem-2',
          body: { role: 'PROCUREMENT' },
        },
      ]);
    });
    expect(await screen.findByText('Grace Hopper is now Procurement.')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Role for Grace Hopper' })).toHaveValue(
      'PROCUREMENT',
    );
  });

  it('reloads the profile after changing your own role', async () => {
    const otherAdmin = { ...grace, role: 'ADMIN' as const };
    renderMembersPage('ADMIN', [ada, otherAdmin]);
    const user = userEvent.setup();
    // Install after the bootstrap settled so only the post-change reload is counted.
    await findTable();
    let meCalls = 0;
    server.use(
      http.get('*/api/v1/auth/me', () => {
        meCalls += 1;
        return HttpResponse.json(buildMe({ memberships: [buildMembership({ role: 'ENGINEER' })] }));
      }),
    );

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Role for Ada Lovelace' }),
      'Engineer',
    );

    await waitFor(() => {
      expect(meCalls).toBe(1);
    });
  });
});

describe('removing a member', () => {
  it('confirms with the name, email and role of the member', async () => {
    renderMembersPage();
    const { dialog } = await openRemoveDialog('Grace Hopper');

    expect(within(dialog).getByText('Grace Hopper')).toBeInTheDocument();
    expect(within(dialog).getByText('grace@example.com')).toBeInTheDocument();
    expect(within(dialog).getByText('Engineer')).toBeInTheDocument();
    expect(within(dialog).queryByText('Linus Torvalds')).not.toBeInTheDocument();
    expect(dialog).toHaveAccessibleDescription(/lose access to Acme Pumps/);
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  it('Cancel closes the dialog, sends no DELETE and returns focus to the trigger', async () => {
    const { api } = renderMembersPage();
    const { user, trigger, dialog } = await openRemoveDialog('Grace Hopper');

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(api.mutations).toEqual([]);
    expect(screen.getByRole('rowheader', { name: 'Grace Hopper' })).toBeInTheDocument();
  });

  it('Escape closes the dialog and sends no DELETE', async () => {
    const { api } = renderMembersPage();
    const { user, trigger } = await openRemoveDialog('Grace Hopper');

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(api.mutations).toEqual([]);
  });

  it('"Remove member" sends DELETE and the row disappears', async () => {
    const { api } = renderMembersPage();
    const { user, dialog } = await openRemoveDialog('Grace Hopper');

    await user.click(within(dialog).getByRole('button', { name: 'Remove member' }));

    await waitFor(() => {
      expect(screen.queryByRole('rowheader', { name: 'Grace Hopper' })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.mutations).toEqual([
      { method: 'DELETE', path: '/api/v1/workspaces/ws-1/members/mem-2', body: null },
    ]);
    expect(screen.getByRole('rowheader', { name: 'Linus Torvalds' })).toBeInTheDocument();
    expect(screen.getByText('Removed Grace Hopper from Acme Pumps.')).toBeInTheDocument();
  });

  it('keeps the dialog open with the LAST_ADMIN message when the server refuses', async () => {
    // Two admins on screen, but the other was demoted meanwhile: the server still says no.
    const otherAdmin = { ...grace, role: 'ADMIN' as const };
    renderMembersPage('ADMIN', [ada, otherAdmin]);
    server.use(
      http.delete('*/api/v1/workspaces/ws-1/members/:memberId', () =>
        HttpResponse.json(errorEnvelope('LAST_ADMIN', 'Cannot remove the last admin.'), {
          status: 409,
        }),
      ),
    );
    const { user, dialog } = await openRemoveDialog('Grace Hopper');

    await user.click(within(dialog).getByRole('button', { name: 'Remove member' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'A workspace must keep at least one admin.',
    );
    expect(screen.getByRole('rowheader', { name: 'Grace Hopper' })).toBeInTheDocument();
  });
});

describe('last admin protection', () => {
  it('blocks demoting or removing the only admin, with an explanation', async () => {
    const { api } = renderMembersPage();
    await findTable();

    const adaRole = screen.getByRole('combobox', { name: 'Role for Ada Lovelace' });
    const adaRemove = screen.getByRole('button', { name: 'Remove Ada Lovelace' });
    expect(adaRole).toBeDisabled();
    expect(adaRemove).toBeDisabled();
    expect(adaRole).toHaveAccessibleDescription(/This is the only admin/);
    expect(adaRemove).toHaveAccessibleDescription(/This is the only admin/);
    // Other members stay editable.
    expect(screen.getByRole('combobox', { name: 'Role for Grace Hopper' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Remove Grace Hopper' })).toBeEnabled();
    expect(api.mutations).toEqual([]);
  });

  it('shows the server LAST_ADMIN error inline when a demotion is refused', async () => {
    const otherAdmin = { ...grace, role: 'ADMIN' as const };
    renderMembersPage('ADMIN', [ada, otherAdmin]);
    server.use(
      http.patch('*/api/v1/workspaces/ws-1/members/:memberId', () =>
        HttpResponse.json(errorEnvelope('LAST_ADMIN', 'Cannot demote the last admin.'), {
          status: 409,
        }),
      ),
    );
    const user = userEvent.setup();
    await findTable();
    const graceRole = screen.getByRole('combobox', { name: 'Role for Grace Hopper' });
    expect(screen.queryByText(/This is the only admin/)).not.toBeInTheDocument();

    await user.selectOptions(graceRole, 'Viewer');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Could not change Grace Hopper's role. A workspace must keep at least one admin.",
    );
    expect(graceRole).toHaveValue('ADMIN');
  });
});

describe('MembersPage as VIEWER', () => {
  it('shows a read-only table without the add form, role pickers or Remove buttons', async () => {
    renderMembersPage('VIEWER');

    const table = await findTable();
    expect(within(rowFor(table, 'Grace Hopper')).getByText('Engineer')).toBeInTheDocument();
    expect(within(rowFor(table, 'Ada Lovelace')).getByText('Admin')).toBeInTheDocument();
    expect(within(table).queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Remove/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Add a member' })).not.toBeInTheDocument();
    expect(screen.getByText(/Only workspace admins can/)).toBeInTheDocument();
  });
});

describe('MembersPage for a workspace the user cannot open', () => {
  it('shows the not-found state', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace({ id: 'ws-1' }));
    renderRoutes(routes, '/workspaces/ws-other/members');

    expect(await screen.findByRole('heading', { name: 'Workspace not found' })).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
