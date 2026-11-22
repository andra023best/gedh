import { useEffect, useState, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { AuthContext, useAuth, type AuthState } from './authContext';
import { getUser, refresh, subscribe, type Role } from './session';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: getUser(), consentRequired: false, ready: false });

  useEffect(() => {
    const unsubscribe = subscribe((s) => setState((prev) => ({ ...prev, ...s })));
    // Після перезавантаження сторінки токена в пам'яті немає — пробуємо відновити сесію за cookie
    void refresh().finally(() => setState((prev) => ({ ...prev, ready: true })));
    return () => {
      unsubscribe();
    };
  }, []);

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}

/** Сторінка лише для входу (і за потреби — для певних ролей). Права все одно перевіряє сервер (NFR-16). */
export function RequireAuth({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <p>Завантаження…</p>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (roles && !roles.includes(user.role)) return <p className="error">Ваша роль не дає доступу до цієї сторінки.</p>;
  return <>{children}</>;
}
