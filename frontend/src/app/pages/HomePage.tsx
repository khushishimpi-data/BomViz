import { ChevronRight } from 'lucide-react';
import { Fragment } from 'react';
import { Navigate } from 'react-router';

import { homePathFor } from '@/features/auth/last-workspace';
import { useAuth } from '@/features/auth/useAuth';

const WORKFLOW_STEPS = ['Upload', 'Map', 'Validate', 'Preview', 'Import', 'View'] as const;

/** Public landing page; signed-in users go straight to their workspace. */
export function HomePage() {
  const { status, memberships } = useAuth();
  if (status === 'authenticated') return <Navigate to={homePathFor(memberships)} replace />;

  return (
    <section aria-labelledby="home-title" className="space-y-6">
      <div className="space-y-2">
        <h1 id="home-title" className="text-2xl font-semibold tracking-tight">
          Manufacturing BOMs, made understandable
        </h1>
        <p className="max-w-2xl text-muted-foreground">
          Upload Excel or CSV bills of materials, map and validate them, then explore them as an
          indented tree, a purchasing roll-up, a where-used graph or a revision diff.
        </p>
      </div>
      <ol aria-label="Import workflow" className="flex flex-wrap items-center gap-2 text-sm">
        {WORKFLOW_STEPS.map((step, index) => (
          <Fragment key={step}>
            <li className="rounded-md border bg-card px-3 py-1.5 font-medium">{step}</li>
            {index < WORKFLOW_STEPS.length - 1 && (
              <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
            )}
          </Fragment>
        ))}
      </ol>
    </section>
  );
}
