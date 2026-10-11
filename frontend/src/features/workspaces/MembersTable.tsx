import { useState } from 'react';
import { useNavigate } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Select } from '@/components/ui/select';
import { useAuth } from '@/features/auth/useAuth';

import {
  describeWorkspaceError,
  isWorkspaceRole,
  ROLE_LABELS,
  useRemoveMember,
  useUpdateMemberRole,
  WORKSPACE_ROLES,
  type Member,
  type Page,
  type WorkspaceRole,
} from './api';

const LAST_ADMIN_HINT_ID = 'last-admin-hint';
const LAST_ADMIN_HINT =
  'This is the only admin. Make another member an admin before changing their role or removing them.';

export interface MembersTableProps {
  workspaceId: string;
  workspaceName: string;
  members: Page<Member>;
  /** ADMIN only; everyone else gets a read-only table. */
  canManage: boolean;
}

/** Member list with per-row role change and removal for admins. */
export function MembersTable({
  workspaceId,
  workspaceName,
  members,
  canManage,
}: MembersTableProps) {
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const updateRole = useUpdateMemberRole(workspaceId);
  const removeMember = useRemoveMember(workspaceId);
  const [roleError, setRoleError] = useState<string | null>(null);
  const [toRemove, setToRemove] = useState<Member | null>(null);
  const [announcement, setAnnouncement] = useState('');

  // Only trust the count when every member is loaded; the server enforces LAST_ADMIN anyway.
  const complete = members.items.length === members.total;
  const adminCount = members.items.filter((member) => member.role === 'ADMIN').length;
  const isLastAdmin = (member: Member): boolean =>
    complete && member.role === 'ADMIN' && adminCount === 1;

  async function handleRoleChange(member: Member, role: WorkspaceRole): Promise<void> {
    if (role === member.role) return;
    setRoleError(null);
    setAnnouncement('');
    try {
      await updateRole.mutateAsync({ memberId: member.id, role });
      setAnnouncement(`${member.full_name} is now ${ROLE_LABELS[role]}.`);
      if (member.user_id === user?.id) await refreshUser().catch(() => undefined);
    } catch (error) {
      setRoleError(`Could not change ${member.full_name}'s role. ${describeWorkspaceError(error)}`);
    }
  }

  function closeDialog(): void {
    setToRemove(null);
    removeMember.reset();
  }

  async function handleConfirmRemove(member: Member): Promise<void> {
    try {
      await removeMember.mutateAsync(member.id);
    } catch {
      return; // Shown inside the dialog via `removeMember.error`.
    }
    closeDialog();
    setAnnouncement(`Removed ${member.full_name} from ${workspaceName}.`);
    if (member.user_id === user?.id) {
      // Removing yourself ends your access to this workspace.
      await refreshUser().catch(() => undefined);
      void navigate('/workspaces', { replace: true });
    }
  }

  const pendingRoleFor = updateRole.isPending ? updateRole.variables.memberId : null;

  return (
    <div className="space-y-4">
      <p role="status" className="sr-only">
        {announcement}
      </p>
      {roleError && (
        <Alert variant="destructive">
          <AlertDescription>{roleError}</AlertDescription>
        </Alert>
      )}
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Members of {workspaceName}</caption>
          <thead className="border-b text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">
                Name
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Email
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Role
              </th>
              {canManage && (
                <th scope="col" className="px-4 py-2 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y">
            {members.items.map((member) => {
              const lastAdmin = isLastAdmin(member);
              const isSelf = member.user_id === user?.id;
              return (
                <tr key={member.id}>
                  <th scope="row" className="px-4 py-2 font-medium">
                    {member.full_name}
                    {isSelf && <span className="font-normal text-muted-foreground"> (you)</span>}
                  </th>
                  <td className="px-4 py-2">{member.email}</td>
                  <td className="px-4 py-2">
                    {canManage ? (
                      <div className="w-40">
                        <Select
                          aria-label={`Role for ${member.full_name}`}
                          aria-describedby={lastAdmin ? LAST_ADMIN_HINT_ID : undefined}
                          value={member.role}
                          disabled={lastAdmin || pendingRoleFor === member.id}
                          onChange={(event) => {
                            const { value } = event.target;
                            if (isWorkspaceRole(value)) void handleRoleChange(member, value);
                          }}
                        >
                          {WORKSPACE_ROLES.map((role) => (
                            <option key={role} value={role}>
                              {ROLE_LABELS[role]}
                            </option>
                          ))}
                        </Select>
                      </div>
                    ) : (
                      ROLE_LABELS[member.role]
                    )}
                  </td>
                  {canManage && (
                    <td className="px-4 py-2 text-right">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={lastAdmin}
                        aria-describedby={lastAdmin ? LAST_ADMIN_HINT_ID : undefined}
                        onClick={() => {
                          setToRemove(member);
                        }}
                      >
                        Remove<span className="sr-only"> {member.full_name}</span>
                      </Button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {canManage && adminCount === 1 && complete && (
        <p id={LAST_ADMIN_HINT_ID} className="text-sm text-muted-foreground">
          {LAST_ADMIN_HINT}
        </p>
      )}
      <ConfirmDialog
        open={toRemove !== null}
        title="Remove member?"
        description={`They will lose access to ${workspaceName}. Their account and past activity are kept.`}
        confirmLabel={removeMember.isPending ? 'Removing…' : 'Remove member'}
        pending={removeMember.isPending}
        error={removeMember.error ? describeWorkspaceError(removeMember.error) : null}
        onCancel={closeDialog}
        onConfirm={() => {
          if (toRemove) void handleConfirmRemove(toRemove);
        }}
      >
        {toRemove && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-md border px-4 py-3 text-sm">
            <dt className="text-muted-foreground">Name</dt>
            <dd className="font-medium">{toRemove.full_name}</dd>
            <dt className="text-muted-foreground">Email</dt>
            <dd>{toRemove.email}</dd>
            <dt className="text-muted-foreground">Role</dt>
            <dd>{ROLE_LABELS[toRemove.role]}</dd>
          </dl>
        )}
      </ConfirmDialog>
    </div>
  );
}
