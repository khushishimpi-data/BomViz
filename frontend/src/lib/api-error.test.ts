import { describe, expect, it } from 'vitest';

import { ApiError, parseRetryAfter, toApiError } from './api-error';

function throttledResponse(headers: Record<string, string>): Response {
  return new Response(
    JSON.stringify({
      error: {
        code: 'LOGIN_THROTTLED',
        message: 'Too many sign-in attempts. Please wait and try again.',
        details: [],
        request_id: 'req-1',
      },
    }),
    { status: 429, headers: { 'Content-Type': 'application/json', ...headers } },
  );
}

describe('parseRetryAfter', () => {
  it('reads delta-seconds', () => {
    expect(parseRetryAfter('120')).toBe(120);
    expect(parseRetryAfter(' 0 ')).toBe(0);
  });

  it('converts an HTTP date into whole seconds from now', () => {
    const now = Date.parse('2026-10-11T12:00:00Z');

    expect(parseRetryAfter('Sun, 11 Oct 2026 12:00:30 GMT', now)).toBe(30);
    expect(parseRetryAfter('Sun, 11 Oct 2026 11:59:00 GMT', now)).toBe(0);
  });

  it('returns null when the header is missing or malformed', () => {
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter('soon')).toBeNull();
    expect(parseRetryAfter('-5')).toBeNull();
  });
});

describe('toApiError', () => {
  it('carries the Retry-After seconds alongside the envelope', async () => {
    const error = await toApiError(throttledResponse({ 'Retry-After': '45' }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 429,
      code: 'LOGIN_THROTTLED',
      message: 'Too many sign-in attempts. Please wait and try again.',
      retryAfterSeconds: 45,
    });
  });

  it('leaves retryAfterSeconds null without the header', async () => {
    const error = await toApiError(throttledResponse({}));

    expect(error.retryAfterSeconds).toBeNull();
  });
});
