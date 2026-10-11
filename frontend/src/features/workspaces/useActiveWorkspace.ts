import type { UseQueryResult } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useParams } from 'react-router';

import { writeLastWorkspaceId } from '@/features/auth/last-workspace';

import { useWorkspace, type Workspace } from './api';

export interface ActiveWorkspace {
  readonly workspaceId: string;
  readonly query: UseQueryResult<Workspace>;
}

/**
 * The workspace named by the `:workspaceId` route param. A successful load records it as the
 * last-used workspace, so only workspaces the user can actually open are remembered.
 */
export function useActiveWorkspace(): ActiveWorkspace {
  const { workspaceId = '' } = useParams();
  const query = useWorkspace(workspaceId);
  const loadedId = query.data?.id;

  useEffect(() => {
    if (loadedId !== undefined) writeLastWorkspaceId(loadedId);
  }, [loadedId]);

  return { workspaceId, query };
}
