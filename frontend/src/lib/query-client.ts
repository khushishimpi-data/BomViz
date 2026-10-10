import { QueryClient } from '@tanstack/react-query';

import { ApiError } from '@/lib/api-client';

const MAX_RETRIES = 2;

/** Client errors (4xx) are deterministic, so only network and 5xx failures are retried. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < MAX_RETRIES;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: 30_000, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}
