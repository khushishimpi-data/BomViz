import { useQuery } from '@tanstack/react-query';

import { toApiError } from '@/lib/api-client';

export type DependencyState = 'ok' | 'error';

export interface ReadinessResponse {
  status: 'ok' | 'degraded';
  checks: Record<string, DependencyState>;
}

const POLL_INTERVAL_MS = 30_000;

/**
 * Backend readiness. A 503 still carries a readiness body, so it is returned as data
 * rather than thrown: the UI shows which dependency is down.
 */
async function fetchReadiness(): Promise<ReadinessResponse> {
  const response = await fetch('/health/ready', { headers: { Accept: 'application/json' } });
  if (!response.ok && response.status !== 503) throw await toApiError(response);
  return (await response.json()) as ReadinessResponse;
}

export function useReadiness() {
  return useQuery({
    queryKey: ['system', 'readiness'],
    queryFn: fetchReadiness,
    refetchInterval: POLL_INTERVAL_MS,
    retry: false,
  });
}
