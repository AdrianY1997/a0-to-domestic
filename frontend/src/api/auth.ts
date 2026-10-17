import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api, onAuthRequired, setCsrfToken } from './client';
import type { components } from './schema';

export type SessionOut = components['schemas']['SessionOut'];

export const SESSION_KEY = ['session'] as const;

export const getSession = async () => {
  const session = await api.get<SessionOut>('/auth/session');
  setCsrfToken(session.csrf_token ?? null);
  return session;
};
export const login = async (password: string) => {
  const session = await api.post<SessionOut>('/auth/login', { password });
  setCsrfToken(session.csrf_token ?? null);
  return session;
};
export const logout = async () => {
  await api.post('/auth/logout');
  setCsrfToken(null);
};

export const useSession = () =>
  useQuery({ queryKey: SESSION_KEY, queryFn: getSession, staleTime: Infinity, retry: false });

/** Any 401 `AUTH_REQUIRED` marks the session as lost, so the guard sends the user to /login. */
export function useAuthRequiredWatcher() {
  const qc = useQueryClient();
  useEffect(() => {
    onAuthRequired(() => {
      setCsrfToken(null);
      qc.setQueryData<SessionOut>(SESSION_KEY, (old) => ({
        auth_mode: old?.auth_mode ?? 'password',
        authenticated: false,
        csrf_token: null,
      }));
    });
    return () => onAuthRequired(null);
  }, [qc]);
}
