import { Link, useMatch, useNavigate } from 'react-router';

import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { writeLastWorkspaceId } from '@/features/auth/last-workspace';
import { useAuth } from '@/features/auth/useAuth';

const SWITCHER_ID = 'workspace-switcher';

/**
 * Header control listing the signed-in user's workspaces (from `/auth/me` memberships, so it
 * needs no extra request). Choosing one opens it and remembers it as last used.
 */
export function WorkspaceSwitcher() {
  const { status, memberships } = useAuth();
  const navigate = useNavigate();
  // The header sits outside the workspace routes, so read the id from the URL directly.
  const match = useMatch('/workspaces/:workspaceId/*');
  const currentId = match?.params.workspaceId ?? '';

  if (status !== 'authenticated') return null;

  const isMember = memberships.some((membership) => membership.workspace_id === currentId);

  function handleChange(workspaceId: string): void {
    if (workspaceId === '') return;
    writeLastWorkspaceId(workspaceId);
    void navigate(`/workspaces/${encodeURIComponent(workspaceId)}`);
  }

  return (
    <nav aria-label="Workspaces" className="flex items-center gap-2 text-sm">
      <Label htmlFor={SWITCHER_ID}>Workspace</Label>
      <div className="w-48">
        <Select
          id={SWITCHER_ID}
          value={isMember ? currentId : ''}
          onChange={(event) => {
            handleChange(event.target.value);
          }}
        >
          <option value="" disabled>
            {memberships.length === 0 ? 'No workspaces yet' : 'Choose a workspace'}
          </option>
          {memberships.map((membership) => (
            <option key={membership.workspace_id} value={membership.workspace_id}>
              {membership.workspace_name}
            </option>
          ))}
        </Select>
      </div>
      <Link
        to="/workspaces"
        className="font-medium whitespace-nowrap text-primary underline-offset-4 hover:underline"
      >
        All workspaces
      </Link>
    </nav>
  );
}
