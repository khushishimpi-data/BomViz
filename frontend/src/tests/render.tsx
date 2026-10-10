import { QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router';

import { createQueryClient } from '@/lib/query-client';

/** Render a route tree at `path` with a fresh query client (no shared cache between tests). */
export function renderRoutes(routes: RouteObject[], path = '/'): RenderResult {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/** Render a single component with a fresh query client. */
export function renderWithQuery(ui: ReactElement): RenderResult {
  return render(<QueryClientProvider client={createQueryClient()}>{ui}</QueryClientProvider>);
}
