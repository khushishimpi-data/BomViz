import { apiRequest } from '@/lib/api-client';

export type WorkspaceRole = 'ADMIN' | 'ENGINEER' | 'PROCUREMENT' | 'PRODUCTION' | 'VIEWER';

export interface User {
  readonly id: string;
  readonly email: string;
  readonly full_name: string;
}

export interface Membership {
  readonly workspace_id: string;
  readonly workspace_name: string;
  readonly workspace_slug: string;
  readonly role: WorkspaceRole;
}

export interface MeResponse extends User {
  readonly memberships: readonly Membership[];
}

export interface LoginResponse {
  readonly access_token: string;
  readonly token_type: 'bearer';
  readonly expires_in: number;
  readonly user: User;
}

export interface LoginInput {
  email: string;
  password: string;
}

export interface RegisterInput extends LoginInput {
  full_name: string;
}

const AUTH_BASE = '/api/v1/auth';

export function login(input: LoginInput): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('POST', `${AUTH_BASE}/login`, { body: input });
}

export function register(input: RegisterInput): Promise<User> {
  return apiRequest<User>('POST', `${AUTH_BASE}/register`, { body: input });
}

export function logout(): Promise<undefined> {
  return apiRequest<undefined>('POST', `${AUTH_BASE}/logout`);
}

export function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  return apiRequest<MeResponse>('GET', `${AUTH_BASE}/me`, signal ? { signal } : {});
}
