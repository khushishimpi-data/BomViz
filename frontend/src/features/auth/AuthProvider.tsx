import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import {
  clearSession,
  refreshAccessToken,
  setSession,
  subscribe,
  withRefreshLock,
} from '@/lib/auth-session';

import * as authApi from './api';
import type { MeResponse, Membership, User } from './api';
import { AuthContext, type AuthContextValue, type AuthStatus } from './useAuth';

interface AuthState {
  readonly status: AuthStatus;
  readonly user: User | null;
  readonly memberships: readonly Membership[];
  readonly signedOut: boolean;
}

const LOADING: AuthState = { status: 'loading', user: null, memberships: [], signedOut: false };
const ANONYMOUS: AuthState = { status: 'anonymous', user: null, memberships: [], signedOut: false };
const SIGNED_OUT: AuthState = { ...ANONYMOUS, signedOut: true };

function authenticated(me: MeResponse): AuthState {
  const { memberships, ...user } = me;
  return { status: 'authenticated', user, memberships, signedOut: false };
}

export interface AuthProviderProps {
  children: ReactNode;
}

/**
 * Owns the signed-in user. On mount it restores the session from the refresh cookie and loads
 * `/auth/me`; any failure there simply means "anonymous".
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const [state, setState] = useState<AuthState>(LOADING);

  const loadMe = useCallback(async (): Promise<void> => {
    try {
      setState(authenticated(await authApi.fetchMe()));
    } catch (error) {
      clearSession();
      setState(ANONYMOUS);
      throw error;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function bootstrap(): Promise<void> {
      try {
        // A refresh failure leaves the session alone here: `refreshAccessToken` already clears
        // it for an auth rejection, and for a transient one (429, 5xx, network) there is
        // nothing to clear — the token lives only in memory and the HttpOnly refresh cookie
        // stays with the browser, so signing in again (or a reload) can still use it.
        await refreshAccessToken();
      } catch {
        if (!cancelled) setState(ANONYMOUS);
        return;
      }
      try {
        const me = await authApi.fetchMe(controller.signal);
        if (!cancelled) setState(authenticated(me));
      } catch {
        if (cancelled) return;
        // A token without a loadable profile is of no use to the UI.
        clearSession();
        setState(ANONYMOUS);
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  useEffect(
    () =>
      subscribe(({ token, userChanged }) => {
        // The API client clears the session when a refresh is rejected mid-use; reflect that.
        if (token === null) {
          setState((current) => (current.user === null ? current : ANONYMOUS));
          return;
        }
        // The token now belongs to someone else: show their profile, not the previous one.
        // A failure has already left the provider anonymous; nothing more to report.
        if (userChanged) void loadMe().catch(() => undefined);
      }),
    [loadMe],
  );

  const login = useCallback<AuthContextValue['login']>(
    async (input) => {
      const response = await authApi.login(input);
      setSession(response.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const register = useCallback<AuthContextValue['register']>(
    async (input) => {
      await authApi.register(input);
      await login({ email: input.email, password: input.password });
    },
    [login],
  );

  const logout = useCallback<AuthContextValue['logout']>(async () => {
    try {
      // Same lock as refresh: a refresh in another tab must not rotate the cookie while this
      // tab revokes its family, or the two would race on the server.
      await withRefreshLock(() => authApi.logout());
    } catch {
      // The local session ends regardless; the refresh cookie expires on its own.
    } finally {
      // Set first: the session listener then sees no user and leaves `signedOut` in place.
      setState(SIGNED_OUT);
      clearSession();
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, register, logout, refreshUser: loadMe }),
    [state, login, register, logout, loadMe],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
