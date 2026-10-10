import { CircleAlert, CircleCheck, LoaderCircle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';

import { useReadiness } from './api';

/** Compact header indicator of backend health, with failing dependencies named. */
export function SystemStatus() {
  const { data, isPending, isError } = useReadiness();

  if (isPending) {
    return (
      <Badge variant="secondary" role="status">
        <LoaderCircle className="size-3 animate-spin" aria-hidden />
        Checking services…
      </Badge>
    );
  }

  if (isError) {
    return (
      <Badge variant="destructive" role="status">
        <CircleAlert className="size-3" aria-hidden />
        Backend unreachable
      </Badge>
    );
  }

  if (data.status === 'ok') {
    return (
      <Badge variant="success" role="status">
        <CircleCheck className="size-3" aria-hidden />
        All systems operational
      </Badge>
    );
  }

  const failing = Object.entries(data.checks)
    .filter(([, state]) => state === 'error')
    .map(([name]) => name);

  return (
    <Badge variant="warning" role="status">
      <CircleAlert className="size-3" aria-hidden />
      Degraded: {failing.join(', ')}
    </Badge>
  );
}
