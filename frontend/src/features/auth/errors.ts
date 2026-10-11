import { ApiError } from '@/lib/api-error';

const GENERIC_MESSAGE = 'Something went wrong. Please try again.';

/**
 * User-facing text for a failed auth request. Only `ApiError.message` (already sanitized by the
 * API client) is shown; rate-limit responses add the wait derived from `Retry-After`.
 */
export function describeAuthError(error: unknown): string {
  if (!(error instanceof ApiError)) return GENERIC_MESSAGE;
  if (error.status === 429 && error.retryAfterSeconds !== null && error.retryAfterSeconds > 0) {
    const seconds = error.retryAfterSeconds;
    return `${error.message} Try again in ${seconds} ${seconds === 1 ? 'second' : 'seconds'}.`;
  }
  return error.message;
}
