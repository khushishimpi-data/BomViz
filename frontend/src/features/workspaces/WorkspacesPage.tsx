import { Link } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

import { describeWorkspaceError, ROLE_LABELS, useWorkspaces } from './api';
import { CreateWorkspaceForm } from './CreateWorkspaceForm';

function WorkspaceList() {
  const { data, error, isPending } = useWorkspaces();

  if (isPending) {
    return (
      <p role="status" className="text-muted-foreground">
        Loading workspaces…
      </p>
    );
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{describeWorkspaceError(error)}</AlertDescription>
      </Alert>
    );
  }
  if (data.items.length === 0) {
    return (
      <p className="text-muted-foreground">
        You are not a member of any workspace yet. Create one, or ask a workspace admin to add you.
      </p>
    );
  }
  return (
    <ul className="divide-y rounded-lg border bg-card">
      {data.items.map((workspace) => (
        <li key={workspace.id} className="flex items-center justify-between gap-4 px-4 py-3">
          <div className="min-w-0">
            <Link
              to={`/workspaces/${encodeURIComponent(workspace.id)}`}
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              {workspace.name}
            </Link>
            <p className="truncate text-sm text-muted-foreground">{workspace.slug}</p>
          </div>
          <Badge variant="secondary">{ROLE_LABELS[workspace.role]}</Badge>
        </li>
      ))}
    </ul>
  );
}

export function WorkspacesPage() {
  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_22rem]">
      <section aria-labelledby="workspaces-title" className="space-y-4">
        <h1 id="workspaces-title" className="text-2xl font-semibold tracking-tight">
          Workspaces
        </h1>
        <WorkspaceList />
      </section>
      <Card className="self-start">
        <CardHeader>
          <CardTitle>Create a workspace</CardTitle>
          <CardDescription>You will be its admin and can add members by email.</CardDescription>
        </CardHeader>
        <CardContent>
          <CreateWorkspaceForm />
        </CardContent>
      </Card>
    </div>
  );
}
