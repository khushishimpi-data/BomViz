import { QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router';

import { AuthProvider } from '@/features/auth/AuthProvider';
import { ClearQueryCacheOnSignOut } from '@/features/auth/ClearQueryCacheOnSignOut';
import { createQueryClient } from '@/lib/query-client';

export type MemoryRouter = ReturnType<typeof createMemoryRouter>;

/**
 * Render a route tree at `path` with a fresh query client (no shared cache between tests) and a
 * real `AuthProvider`. The session starts anonymous unless the test calls `signInAs()` first.
 * The returned `router` exposes the current location for navigation assertions.
 */
export function renderRoutes(
  routes: RouteObject[],
  path = '/',
): RenderResult & { router: MemoryRouter } {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const result = render(
    <QueryClientProvider client={createQueryClient()}>
      <ClearQueryCacheOnSignOut />
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { ...result, router };
}

/** Render a single component with a fresh query client. */
export function renderWithQuery(ui: ReactElement): RenderResult {
  return render(<QueryClientProvider client={createQueryClient()}>{ui}</QueryClientProvider>);
}
