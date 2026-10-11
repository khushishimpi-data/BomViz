import type { Membership } from './api';

/** localStorage key for the last-used workspace id (convenience only, never trusted). */
export const LAST_WORKSPACE_KEY = 'bomviz:last-workspace-id';

export function readLastWorkspaceId(): string | null {
  try {
    return window.localStorage.getItem(LAST_WORKSPACE_KEY);
  } catch {
    // Storage can be disabled (privacy mode, quota); fall back to "no preference".
    return null;
  }
}

export function writeLastWorkspaceId(workspaceId: string): void {
  try {
    window.localStorage.setItem(LAST_WORKSPACE_KEY, workspaceId);
  } catch {
    // Best effort; losing the preference only affects the landing redirect.
  }
}

/**
 * Landing path for a signed-in user: the last-used workspace if they still belong to it,
 * otherwise their first workspace, otherwise the workspace list.
 */
export function homePathFor(memberships: readonly Membership[]): string {
  const lastId = readLastWorkspaceId();
  const target =
    memberships.find((membership) => membership.workspace_id === lastId) ?? memberships[0];
  return target ? `/workspaces/${encodeURIComponent(target.workspace_id)}` : '/workspaces';
}
