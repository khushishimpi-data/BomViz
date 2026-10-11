import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import type { WorkspaceRole } from '@/features/auth/api';
import { apiRequest, ApiError } from '@/lib/api-client';

export type { WorkspaceRole } from '@/features/auth/api';

export interface Workspace {
  readonly id: string;
  readonly name: string;
  readonly slug: string;
  readonly owner_id: string;
  /** The caller's role in this workspace. */
  readonly role: WorkspaceRole;
  readonly created_at: string;
}

export interface Member {
  /** Membership id (used in member mutation paths), not the user id. */
  readonly id: string;
  readonly user_id: string;
  readonly email: string;
  readonly full_name: string;
  readonly role: WorkspaceRole;
  readonly created_at: string;
}

export interface Page<TItem> {
  readonly items: readonly TItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface CreateWorkspaceInput {
  name: string;
  slug?: string;
}

export interface AddMemberInput {
  email: string;
  role: WorkspaceRole;
}

export interface UpdateMemberRoleInput {
  memberId: string;
  role: WorkspaceRole;
}

/** Every role, most privileged first; the order used in role pickers. */
export const WORKSPACE_ROLES: readonly WorkspaceRole[] = [
  'ADMIN',
  'ENGINEER',
  'PROCUREMENT',
  'PRODUCTION',
  'VIEWER',
];

export function isWorkspaceRole(value: string): value is WorkspaceRole {
  return (WORKSPACE_ROLES as readonly string[]).includes(value);
}

export const ROLE_LABELS: Readonly<Record<WorkspaceRole, string>> = {
  ADMIN: 'Admin',
  ENGINEER: 'Engineer',
  PROCUREMENT: 'Procurement',
  PRODUCTION: 'Production',
  VIEWER: 'Viewer',
};

/** Only ADMIN holds `workspace:manage_members` (see the backend permission policy). */
export function canManageMembers(role: WorkspaceRole | undefined): boolean {
  return role === 'ADMIN';
}

/** The API maximum; workspace and member lists are small, so one page covers them. */
const LIST_LIMIT = 200;
const BASE = '/api/v1/workspaces';

function workspacePath(workspaceId: string): string {
  return `${BASE}/${encodeURIComponent(workspaceId)}`;
}

function membersPath(workspaceId: string): string {
  return `${workspacePath(workspaceId)}/members`;
}

function memberPath(workspaceId: string, memberId: string): string {
  return `${membersPath(workspaceId)}/${encodeURIComponent(memberId)}`;
}

export const workspaceKeys = {
  all: ['workspaces'] as const,
  detail: (workspaceId: string) => ['workspaces', workspaceId] as const,
  members: (workspaceId: string) => ['workspaces', workspaceId, 'members'] as const,
};

export function fetchWorkspaces(signal?: AbortSignal): Promise<Page<Workspace>> {
  return apiRequest<Page<Workspace>>(
    'GET',
    `${BASE}?limit=${LIST_LIMIT}`,
    signal ? { signal } : {},
  );
}

export function fetchWorkspace(workspaceId: string, signal?: AbortSignal): Promise<Workspace> {
  return apiRequest<Workspace>('GET', workspacePath(workspaceId), signal ? { signal } : {});
}

export function createWorkspace(input: CreateWorkspaceInput): Promise<Workspace> {
  return apiRequest<Workspace>('POST', BASE, { body: input });
}

export function fetchMembers(workspaceId: string, signal?: AbortSignal): Promise<Page<Member>> {
  return apiRequest<Page<Member>>(
    'GET',
    `${membersPath(workspaceId)}?limit=${LIST_LIMIT}`,
    signal ? { signal } : {},
  );
}

export function addMember(workspaceId: string, input: AddMemberInput): Promise<Member> {
  return apiRequest<Member>('POST', membersPath(workspaceId), { body: input });
}

export function updateMemberRole(
  workspaceId: string,
  { memberId, role }: UpdateMemberRoleInput,
): Promise<Member> {
  return apiRequest<Member>('PATCH', memberPath(workspaceId, memberId), { body: { role } });
}

export function removeMember(workspaceId: string, memberId: string): Promise<undefined> {
  return apiRequest<undefined>('DELETE', memberPath(workspaceId, memberId));
}

export function useWorkspaces(): UseQueryResult<Page<Workspace>> {
  return useQuery({
    queryKey: workspaceKeys.all,
    queryFn: ({ signal }) => fetchWorkspaces(signal),
  });
}

export function useWorkspace(workspaceId: string): UseQueryResult<Workspace> {
  return useQuery({
    queryKey: workspaceKeys.detail(workspaceId),
    queryFn: ({ signal }) => fetchWorkspace(workspaceId, signal),
  });
}

export function useMembers(workspaceId: string, enabled = true): UseQueryResult<Page<Member>> {
  return useQuery({
    queryKey: workspaceKeys.members(workspaceId),
    queryFn: ({ signal }) => fetchMembers(workspaceId, signal),
    enabled,
  });
}

export function useCreateWorkspace(): UseMutationResult<Workspace, Error, CreateWorkspaceInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createWorkspace,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.all }),
  });
}

export function useAddMember(
  workspaceId: string,
): UseMutationResult<Member, Error, AddMemberInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input) => addMember(workspaceId, input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: workspaceKeys.members(workspaceId) }),
  });
}

export function useUpdateMemberRole(
  workspaceId: string,
): UseMutationResult<Member, Error, UpdateMemberRoleInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input) => updateMemberRole(workspaceId, input),
    // Prefix match: refetches the member list and the workspace detail, since the caller's
    // own role may have changed.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(workspaceId) }),
  });
}

export function useRemoveMember(workspaceId: string): UseMutationResult<undefined, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (memberId) => removeMember(workspaceId, memberId),
    onSuccess: (_result, memberId) => {
      // Drop the row immediately so it disappears without waiting for the refetch.
      queryClient.setQueryData<Page<Member>>(workspaceKeys.members(workspaceId), (page) =>
        page
          ? {
              ...page,
              items: page.items.filter((member) => member.id !== memberId),
              total: page.total - 1,
            }
          : page,
      );
      // Prefix match: also refetches the member list to confirm the server state.
      return queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(workspaceId) });
    },
  });
}

const GENERIC_MESSAGE = 'Something went wrong. Please try again.';

const CODE_MESSAGES: Readonly<Record<string, string>> = {
  USER_NOT_FOUND: 'No BOMViz account uses that email. Ask them to create an account first.',
  ALREADY_MEMBER: 'That person is already a member of this workspace.',
  LAST_ADMIN:
    'A workspace must keep at least one admin. Make another member an admin first, then try again.',
  SLUG_TAKEN: 'That slug is already used by another workspace. Choose a different one.',
  FORBIDDEN: 'You do not have permission to do that in this workspace.',
};

/** User-facing text for a failed workspace request. */
export function describeWorkspaceError(error: unknown): string {
  if (!(error instanceof ApiError)) return GENERIC_MESSAGE;
  return CODE_MESSAGES[error.code] ?? error.message;
}

/** True when the workspace does not exist or the caller is not a member (both 404). */
export function isWorkspaceNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === 'NOT_FOUND';
}
