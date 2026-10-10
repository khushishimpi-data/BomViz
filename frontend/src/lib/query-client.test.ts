import { describe, expect, it } from 'vitest';

import { ApiError } from './api-client';
import { shouldRetry } from './query-client';

const apiError = (status: number) => new ApiError(status, { code: 'X', message: 'x', details: [] });

describe('shouldRetry', () => {
  it('does not retry client errors', () => {
    expect(shouldRetry(0, apiError(404))).toBe(false);
    expect(shouldRetry(0, apiError(422))).toBe(false);
  });

  it('retries server and network errors up to the limit', () => {
    expect(shouldRetry(0, apiError(503))).toBe(true);
    expect(shouldRetry(1, new TypeError('Failed to fetch'))).toBe(true);
    expect(shouldRetry(2, apiError(503))).toBe(false);
  });
});
