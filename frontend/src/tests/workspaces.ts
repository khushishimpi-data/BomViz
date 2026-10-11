import { http, HttpResponse } from 'msw';

import {
  isWorkspaceRole,
  type Member,
  type Page,
  type Workspace,
  type WorkspaceRole,
} from '@/features/workspaces/api';

import { errorEnvelope } from './auth';
import { server } from './server';

/** Defaults match `buildMembership()` / `buildMe()` in `tests/auth.ts` (ws-1, user-1 as ADMIN). */
export function buildWorkspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: 'ws-1',
    name: 'Acme Pumps',
    slug: 'acme-pumps',
    owner_id: 'user-1',
    role: 'ADMIN',
    created_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function buildMember(overrides: Partial<Member> = {}): Member {
  return {
    id: 'mem-1',
    user_id: 'user-1',
    email: 'ada@example.com',
    full_name: 'Ada Lovelace',
    role: 'ADMIN',
    created_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function buildPage<TItem>(items: readonly TItem[]): Page<TItem> {
  return { items, total: items.length, limit: 200, offset: 0 };
}

export interface RecordedRequest {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
}

export interface FakeWorkspaceApi {
  /** Current member list; mutated by PATCH/DELETE like the real backend. */
  members: Member[];
  /** Every mutating request (POST/PATCH/DELETE) in arrival order. */
  readonly mutations: RecordedRequest[];
}

function adminCount(members: readonly Member[]): number {
  return members.filter((member) => member.role === 'ADMIN').length;
}

async function readJson(request: Request): Promise<unknown> {
  return request.json().catch(() => null);
}

function roleFrom(body: unknown): WorkspaceRole | null {
  if (typeof body !== 'object' || body === null || !('role' in body)) return null;
  return typeof body.role === 'string' && isWorkspaceRole(body.role) ? body.role : null;
}

/**
 * An in-memory backend for one workspace: detail, member list, role change and removal
 * (including the LAST_ADMIN guard). Other workspace ids answer 404 `NOT_FOUND`, as for a
 * non-member. Add-member responses are left to each test.
 */
export function installWorkspaceApi(
  workspace: Workspace = buildWorkspace(),
  initialMembers: readonly Member[] = [buildMember()],
): FakeWorkspaceApi {
  const api: FakeWorkspaceApi = { members: [...initialMembers], mutations: [] };
  const notFound = (): Response =>
    HttpResponse.json(errorEnvelope('NOT_FOUND', 'Workspace not found.'), { status: 404 });
  const lastAdmin = (): Response =>
    HttpResponse.json(errorEnvelope('LAST_ADMIN', 'A workspace needs an admin.'), {
      status: 409,
    });

  server.use(
    http.get('*/api/v1/workspaces/:workspaceId', ({ params }) =>
      params.workspaceId === workspace.id ? HttpResponse.json(workspace) : notFound(),
    ),
    http.get('*/api/v1/workspaces/:workspaceId/members', ({ params }) =>
      params.workspaceId === workspace.id ? HttpResponse.json(buildPage(api.members)) : notFound(),
    ),
    http.patch(
      '*/api/v1/workspaces/:workspaceId/members/:memberId',
      async ({ params, request }) => {
        const body = await readJson(request);
        api.mutations.push({ method: 'PATCH', path: new URL(request.url).pathname, body });
        const member = api.members.find((candidate) => candidate.id === params.memberId);
        const role = roleFrom(body);
        if (params.workspaceId !== workspace.id || !member || role === null) return notFound();
        if (member.role === 'ADMIN' && role !== 'ADMIN' && adminCount(api.members) === 1) {
          return lastAdmin();
        }
        const updated = { ...member, role };
        api.members = api.members.map((candidate) =>
          candidate.id === member.id ? updated : candidate,
        );
        return HttpResponse.json(updated);
      },
    ),
    http.delete('*/api/v1/workspaces/:workspaceId/members/:memberId', ({ params, request }) => {
      api.mutations.push({ method: 'DELETE', path: new URL(request.url).pathname, body: null });
      const member = api.members.find((candidate) => candidate.id === params.memberId);
      if (params.workspaceId !== workspace.id || !member) return notFound();
      if (member.role === 'ADMIN' && adminCount(api.members) === 1) return lastAdmin();
      api.members = api.members.filter((candidate) => candidate.id !== member.id);
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return api;
}
