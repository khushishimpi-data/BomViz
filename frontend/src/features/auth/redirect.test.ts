import { describe, expect, it } from 'vitest';

import { loginPathFor, safeRedirectPath } from './redirect';

describe('safeRedirectPath', () => {
  it.each(['/', '/workspaces', '/workspaces/abc/products?q=pump#top'])('accepts %s', (path) => {
    expect(safeRedirectPath(path)).toBe(path);
  });

  it.each([
    null,
    '',
    'workspaces',
    'https://evil.com',
    '//evil.com',
    '/\\evil.com',
    '/\t/evil.com',
    'javascript:alert(1)',
  ])('rejects %j', (path) => {
    expect(safeRedirectPath(path)).toBeNull();
  });
});

describe('loginPathFor', () => {
  it('encodes the return path as the next parameter', () => {
    expect(loginPathFor('/workspaces/abc?tab=x')).toBe('/login?next=%2Fworkspaces%2Fabc%3Ftab%3Dx');
  });
});
