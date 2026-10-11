import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

import { subscribe } from '@/lib/auth-session';

/**
 * Drops every cached query when the session ends — an explicit sign-out or a rejected refresh
 * mid-use — or when the token switches to a different user without signing out (another tab
 * signed in as someone else), so nobody ever sees the previous user's data.
 * Renders nothing; mount it once inside `QueryClientProvider`.
 */
export function ClearQueryCacheOnSignOut(): null {
  const queryClient = useQueryClient();
  useEffect(
    () =>
      subscribe(({ token, userChanged }) => {
        if (token === null) {
          // Signed-in pages unmount on sign-out, so nothing is left observing the cache.
          queryClient.clear();
        } else if (userChanged) {
          // The pages stay mounted, and `clear()` would leave their observers showing the
          // previous user's data. Resetting wipes every query and refetches the active ones
          // with the new user's token.
          queryClient.getMutationCache().clear();
          void queryClient.resetQueries();
        }
      }),
    [queryClient],
  );
  return null;
}
