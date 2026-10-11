import { Link } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

import { AddMemberForm } from './AddMemberForm';
import { canManageMembers, describeWorkspaceError, useMembers } from './api';
import { MembersTable } from './MembersTable';
import { useActiveWorkspace } from './useActiveWorkspace';
import { WorkspaceQueryState } from './WorkspaceQueryState';

export function MembersPage() {
  const { workspaceId, query } = useActiveWorkspace();
  const workspace = query.data;
  const members = useMembers(workspaceId, workspace !== undefined);

  if (workspace === undefined) return <WorkspaceQueryState error={query.error} />;

  const canManage = canManageMembers(workspace.role);
  return (
    <section aria-labelledby="members-title" className="space-y-6">
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">
          <Link
            to={`/workspaces/${encodeURIComponent(workspaceId)}`}
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            {workspace.name}
          </Link>
        </p>
        <h1 id="members-title" className="text-2xl font-semibold tracking-tight">
          Members
        </h1>
        {!canManage && (
          <p className="text-sm text-muted-foreground">
            Only workspace admins can add, change or remove members.
          </p>
        )}
      </div>

      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>Add a member</CardTitle>
            <CardDescription>They need a BOMViz account with this email already.</CardDescription>
          </CardHeader>
          <CardContent>
            <AddMemberForm workspaceId={workspaceId} />
          </CardContent>
        </Card>
      )}

      {members.isPending && (
        <p role="status" className="text-muted-foreground">
          Loading members…
        </p>
      )}
      {members.error && (
        <Alert variant="destructive">
          <AlertDescription>{describeWorkspaceError(members.error)}</AlertDescription>
        </Alert>
      )}
      {members.data && (
        <MembersTable
          workspaceId={workspaceId}
          workspaceName={workspace.name}
          members={members.data}
          canManage={canManage}
        />
      )}
    </section>
  );
}
