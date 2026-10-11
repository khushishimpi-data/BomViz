import { Link } from 'react-router';

import { Badge } from '@/components/ui/badge';

import { ROLE_LABELS } from './api';
import { useActiveWorkspace } from './useActiveWorkspace';
import { WorkspaceQueryState } from './WorkspaceQueryState';

const LINK_CLASS = 'font-medium text-primary underline-offset-4 hover:underline';

/** Workspace index: name, slug, your role, and links to Products, BOMs and Members. */
export function WorkspaceOverviewPage() {
  const { workspaceId, query } = useActiveWorkspace();
  const workspace = query.data;
  if (workspace === undefined) return <WorkspaceQueryState error={query.error} />;

  const base = `/workspaces/${encodeURIComponent(workspaceId)}`;
  return (
    <section aria-labelledby="workspace-title" className="space-y-6">
      <div className="space-y-2">
        <h1 id="workspace-title" className="text-2xl font-semibold tracking-tight">
          {workspace.name}
        </h1>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>{workspace.slug}</span>
          <Badge variant="secondary">Your role: {ROLE_LABELS[workspace.role]}</Badge>
        </p>
      </div>
      <nav aria-label="Workspace sections">
        <ul className="flex gap-4">
          <li>
            <Link to={`${base}/products`} className={LINK_CLASS}>
              Products
            </Link>
          </li>
          <li>
            <Link to={`${base}/boms`} className={LINK_CLASS}>
              BOMs
            </Link>
          </li>
          <li>
            <Link to={`${base}/members`} className={LINK_CLASS}>
              Members
            </Link>
          </li>
        </ul>
      </nav>
    </section>
  );
}
