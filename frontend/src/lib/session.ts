// Сесія в браузері (контракт: «Автентифікація»).
// - Токен доступу (15 хв) живе лише в пам'яті сторінки, не в localStorage.
// - Токен оновлення — у cookie HttpOnly, JavaScript його не бачить; оновлення потребує X-CSRF-Token,
//   значення якого береться з cookie gedh_csrf (подвійне подання).
// - Кілька одночасних відповідей 401 запускають лише одне оновлення (спільний проміс).

export type Role = 'ANALYST' | 'ORG_REPRESENTATIVE' | 'DATA_PROVIDER' | 'ADMIN' | 'DG_OFFICER';

export type UserBrief = {
  id: string;
  full_name: string;
  role: Role;
  status: string;
  organization: { id: string; name: string; type: string; country_code: string } | null;
};

export type TokenResponse = {
  access_token: string;
  expires_in: number;
  user: UserBrief;
  consent_update_required: boolean;
};

export class ApiError extends Error {
  status: number;
  code: string;
  body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    const code = typeof body.detail === 'string' ? body.detail : status === 422 ? 'VALIDATION_ERROR' : `HTTP_${status}`;
    super(code);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

type Listener = (state: { user: UserBrief | null; consentRequired: boolean }) => void;

let accessToken: string | null = null;
let currentUser: UserBrief | null = null;
let consentRequired = false;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<Listener>();

function emit() {
  listeners.forEach((l) => l({ user: currentUser, consentRequired }));
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getUser() {
  return currentUser;
}

function readCookie(name: string): string | null {
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

function applyTokens(t: TokenResponse) {
  accessToken = t.access_token;
  currentUser = t.user;
  consentRequired = t.consent_update_required;
  emit();
}

export function clearSession() {
  accessToken = null;
  currentUser = null;
  consentRequired = false;
  emit();
}

async function parse(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: `HTTP_${res.status}` };
  }
}

/** Оновлює токен доступу за cookie; true — сесію відновлено. */
export function refresh(): Promise<boolean> {
  if (refreshing) return refreshing;
  const csrf = readCookie('gedh_csrf');
  if (!csrf) return Promise.resolve(false);
  refreshing = fetch('/api/auth/refresh', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRF-Token': csrf },
  })
    .then(async (res) => {
      if (!res.ok) {
        clearSession();
        return false;
      }
      applyTokens((await res.json()) as TokenResponse);
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/** Запит до API: додає токен, у разі 401 один раз оновлює сесію й повторює запит. */
export async function api<T = unknown>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  if (init.body && typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: 'same-origin' });
  if (res.status === 401 && retry && accessToken && (await refresh())) {
    return api<T>(path, init, false);
  }
  const body = (await parse(res)) as Record<string, unknown>;
  if (!res.ok) {
    if (res.status === 401 && currentUser) clearSession();
    throw new ApiError(res.status, body);
  }
  return body as T;
}

/** Сира відповідь (для файлів) з тим самим токеном і оновленням сесії. */
export async function apiRaw(path: string, retry = true): Promise<Response> {
  const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined;
  const res = await fetch(`/api${path}`, { headers, credentials: 'same-origin' });
  if (res.status === 401 && retry && accessToken && (await refresh())) return apiRaw(path, false);
  if (!res.ok) throw new ApiError(res.status, (await parse(res)) as Record<string, unknown>);
  return res;
}

export const post = <T = unknown>(path: string, data?: unknown) =>
  api<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) });

export async function login(email: string, password: string): Promise<TokenResponse> {
  const t = await post<TokenResponse>('/auth/login', { email, password });
  applyTokens(t);
  return t;
}

export async function logout(): Promise<void> {
  const csrf = readCookie('gedh_csrf');
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    });
  } finally {
    clearSession();
  }
}

export function markConsentAccepted() {
  consentRequired = false;
  emit();
}

/** Лише для тестів. */
export function __setToken(token: string | null, user: UserBrief | null = null) {
  accessToken = token;
  currentUser = user;
}
