import { createContext, useContext } from 'react';

import type { LoginInput, Membership, RegisterInput, User } from './api';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

export interface AuthContextValue {
  readonly status: AuthStatus;
  /** `null` unless `status` is `authenticated`. */
  readonly user: User | null;
  readonly memberships: readonly Membership[];
  /** True after the user signed out in this tab (as opposed to never having had a session). */
  readonly signedOut: boolean;
  /** Sign in and load the profile. Rejects with `ApiError` on failure. */
  login: (input: LoginInput) => Promise<void>;
  /** Create the account, then sign in with the same credentials. */
  register: (input: RegisterInput) => Promise<void>;
  /** End the server session (best effort) and always clear the local one. */
  logout: () => Promise<void>;
  /** Reload the profile and memberships, e.g. after creating or joining a workspace. */
  refreshUser: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error('useAuth must be used inside <AuthProvider>.');
  return value;
}
