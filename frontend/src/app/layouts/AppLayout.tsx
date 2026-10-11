import { Boxes } from 'lucide-react';
import { Link, Outlet } from 'react-router';

import { Button } from '@/components/ui/button';
import { useAuth } from '@/features/auth/useAuth';
import { UserMenu } from '@/features/auth/UserMenu';
import { SystemStatus } from '@/features/system-status/SystemStatus';
import { WorkspaceSwitcher } from '@/features/workspaces/WorkspaceSwitcher';

export function AppLayout() {
  const { status } = useAuth();
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:rounded-md focus:bg-background focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="border-b bg-card">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <Boxes className="size-5 text-primary" aria-hidden />
            BOMViz
          </Link>
          <div className="flex items-center gap-4">
            <SystemStatus />
            <WorkspaceSwitcher />
            {status === 'authenticated' && <UserMenu />}
            {status === 'anonymous' && (
              <nav aria-label="Account" className="flex items-center gap-2">
                <Button asChild variant="ghost" size="sm">
                  <Link to="/login">Sign in</Link>
                </Button>
                <Button asChild size="sm">
                  <Link to="/register">Create account</Link>
                </Button>
              </nav>
            )}
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
