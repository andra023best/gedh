import { createContext, useContext } from 'react';
import type { UserBrief } from './session';

export type AuthState = { user: UserBrief | null; consentRequired: boolean; ready: boolean };

export const AuthContext = createContext<AuthState>({ user: null, consentRequired: false, ready: false });

export function useAuth() {
  return useContext(AuthContext);
}
