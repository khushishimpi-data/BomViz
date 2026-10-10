import { Link } from 'react-router';

import { Button } from '@/components/ui/button';

export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-title" className="space-y-4 py-16 text-center">
      <h1 id="not-found-title" className="text-2xl font-semibold">
        Page not found
      </h1>
      <p className="text-muted-foreground">The page you requested does not exist.</p>
      <Button asChild variant="outline">
        <Link to="/">Back to home</Link>
      </Button>
    </section>
  );
}
