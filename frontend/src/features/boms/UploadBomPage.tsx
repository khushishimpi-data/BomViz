import { Link } from 'react-router';

import { useActiveWorkspace } from '@/features/workspaces/useActiveWorkspace';
import { WorkspaceQueryState } from '@/features/workspaces/WorkspaceQueryState';

import { useCanUploadBoms } from './api';
import { UploadBomForm } from './UploadBomForm';

const LINK_CLASS = 'font-medium text-primary underline-offset-4 hover:underline';

/** `/workspaces/:workspaceId/boms/new`: the upload form for admins and engineers. */
export function UploadBomPage() {
  const { workspaceId, query } = useActiveWorkspace();
  const workspace = query.data;
  const canUpload = useCanUploadBoms(workspaceId);

  if (workspace === undefined) return <WorkspaceQueryState error={query.error} />;

  const base = `/workspaces/${encodeURIComponent(workspaceId)}`;
  return (
    <section aria-labelledby="upload-bom-title" className="space-y-6">
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">
          <Link to={base} className={LINK_CLASS}>
            {workspace.name}
          </Link>
          {' / '}
          <Link to={`${base}/boms`} className={LINK_CLASS}>
            BOMs
          </Link>
        </p>
        <h1 id="upload-bom-title" className="text-2xl font-semibold tracking-tight">
          Upload BOM
        </h1>
      </div>
      {canUpload ? (
        <UploadBomForm workspaceId={workspaceId} />
      ) : (
        <div className="space-y-2">
          <p className="text-muted-foreground">Only admins and engineers can upload BOMs.</p>
          <Link to={`${base}/boms`} className={LINK_CLASS}>
            Back to BOMs
          </Link>
        </div>
      )}
    </section>
  );
}
