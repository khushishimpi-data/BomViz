import { useState } from 'react';

import { Button } from '@/components/ui/button';

import { useAuth } from './useAuth';

/** Signed-in user's name and a Sign out action, shown in the header. */
export function UserMenu() {
  const { user, logout } = useAuth();
  const [pending, setPending] = useState(false);

  if (user === null) return null;

  async function handleSignOut(): Promise<void> {
    setPending(true);
    // Once signed out this menu unmounts and RequireAuth sends the user home.
    await logout();
  }

  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="font-medium" title={user.email}>
        {user.full_name}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pending}
        onClick={() => void handleSignOut()}
      >
        {pending ? 'Signing out…' : 'Sign out'}
      </Button>
    </div>
  );
}
