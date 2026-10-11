import { QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router';

import { AuthProvider } from '@/features/auth/AuthProvider';
import { ClearQueryCacheOnSignOut } from '@/features/auth/ClearQueryCacheOnSignOut';
import { createQueryClient } from '@/lib/query-client';

import { routes } from './routes';

const router = createBrowserRouter(routes);

export function App() {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <ClearQueryCacheOnSignOut />
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  );
}
