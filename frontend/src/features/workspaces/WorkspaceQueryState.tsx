import { Link } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

import { describeWorkspaceError, isWorkspaceNotFound } from './api';

export interface WorkspaceQueryStateProps {
  /** The failed workspace query's error, or `null` while it is still loading. */
  error: Error | null;
}

/**
 * Loading and failure states for a page that needs the active workspace. Non-members get the
 * same 404 as a missing workspace, so both read as "not found".
 */
export function WorkspaceQueryState({ error }: WorkspaceQueryStateProps) {
  if (error === null) {
    return (
      <p role="status" className="py-16 text-center text-muted-foreground">
        Loading workspace…
      </p>
    );
  }
  if (isWorkspaceNotFound(error)) {
    return (
      <section aria-labelledby="workspace-not-found-title" className="space-y-4 py-16 text-center">
        <h1 id="workspace-not-found-title" className="text-2xl font-semibold">
          Workspace not found
        </h1>
        <p className="text-muted-foreground">It does not exist, or you are not a member of it.</p>
        <Button asChild variant="outline">
          <Link to="/workspaces">All workspaces</Link>
        </Button>
      </section>
    );
  }
  return (
    <section aria-labelledby="workspace-error-title" className="space-y-4">
      <h1 id="workspace-error-title" className="text-2xl font-semibold">
        Workspace unavailable
      </h1>
      <Alert variant="destructive">
        <AlertDescription>{describeWorkspaceError(error)}</AlertDescription>
      </Alert>
    </section>
  );
}
