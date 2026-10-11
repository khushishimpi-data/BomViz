/** Where to go after signing in when no (valid) `?next=` is given. */
export const DEFAULT_AFTER_LOGIN = '/';

function hasControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/**
 * Return `next` only if it is a same-origin relative path (`/...`), otherwise `null`.
 * Rejects protocol-relative (`//evil.com`) and backslash (`/\evil.com`) forms, which browsers
 * resolve to another origin, and anything that would not resolve to this origin.
 */
export function safeRedirectPath(next: string | null): string | null {
  if (next === null || !next.startsWith('/')) return null;
  if (next.startsWith('//') || next.startsWith('/\\')) return null;
  // Control characters (tabs, newlines) are stripped by URL parsers and can hide a `//` prefix.
  if (hasControlCharacter(next)) return null;
  try {
    const origin = window.location.origin;
    if (new URL(next, origin).origin !== origin) return null;
  } catch {
    return null;
  }
  return next;
}

/** Build the login URL that brings the user back to `path` afterwards. */
export function loginPathFor(path: string): string {
  return `/login?${new URLSearchParams({ next: path }).toString()}`;
}
