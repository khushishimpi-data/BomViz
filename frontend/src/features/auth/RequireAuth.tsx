import { Navigate, Outlet, useLocation } from 'react-router';

import { loginPathFor } from './redirect';
import { useAuth } from './useAuth';

/** Layout route guard: renders child routes only for a signed-in user. */
export function RequireAuth() {
  const { status, signedOut } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return (
      <p role="status" className="py-16 text-center text-muted-foreground">
        Checking your session…
      </p>
    );
  }
  if (status === 'anonymous') {
    // After an explicit sign-out, go home rather than offering to resume the old page.
    if (signedOut) return <Navigate to="/" replace />;
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={loginPathFor(returnTo)} replace />;
  }
  return <Outlet />;
}
