import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { errorText } from './messages';
import { __setToken, api, ApiError, getUser } from './session';

const user = { id: 'u1', full_name: 'Тест', role: 'ANALYST', status: 'ACTIVE', organization: null } as const;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
  vi.stubGlobal('document', { cookie: 'gedh_csrf=csrf-1' });
});
afterEach(() => {
  vi.unstubAllGlobals();
  __setToken(null);
});

describe('api()', () => {
  it('додає Bearer-токен і повертає тіло', async () => {
    __setToken('t-old', { ...user });
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: 1 }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await api('/me')).toEqual({ ok: 1 });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer t-old');
  });

  it('паралельні 401 запускають лише одне оновлення сесії, потім запити повторюються', async () => {
    __setToken('t-old', { ...user });
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      if (url === '/api/auth/refresh') {
        refreshCalls++;
        expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-1'); // подвійне подання CSRF
        return json(200, { access_token: 't-new', expires_in: 900, user, consent_update_required: false });
      }
      const auth = new Headers(init.headers).get('Authorization');
      return auth === 'Bearer t-new' ? json(200, { url }) : json(401, { detail: 'TOKEN_EXPIRED' });
    });
    vi.stubGlobal('fetch', fetchMock);
    const results = await Promise.all([api('/me'), api('/countries'), api('/admin/registrations')]);
    expect(results.map((r) => (r as { url: string }).url)).toEqual([
      '/api/me',
      '/api/countries',
      '/api/admin/registrations',
    ]);
    expect(refreshCalls).toBe(1);
  });

  it('якщо оновити сесію не вдалося — користувача скинуто, помилка з кодом', async () => {
    __setToken('t-old', { ...user });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/auth/refresh' ? json(401, { detail: 'SESSION_REVOKED' }) : json(401, { detail: 'TOKEN_EXPIRED' }),
      ),
    );
    await expect(api('/me')).rejects.toMatchObject({ status: 401, code: 'TOKEN_EXPIRED' });
    expect(getUser()).toBeNull();
  });
});

describe('errorText()', () => {
  it('перекладає код помилки без технічних деталей (NFR-22)', () => {
    expect(errorText(new ApiError(401, { detail: 'INVALID_CREDENTIALS' }))).toBe('Невірний e-mail або пароль.');
    expect(errorText(new ApiError(403, { detail: 'ACCOUNT_NOT_ACTIVE', status: 'PENDING_REVIEW' }))).toBe(
      'Обліковий запис очікує розгляду адміністратором.',
    );
    expect(errorText(new ApiError(423, { detail: 'ACCOUNT_LOCKED', retry_after: 899 }))).toContain('15 хв');
    expect(errorText(new TypeError('network'))).toBe('Сервер недоступний. Спробуйте ще раз.');
  });
});
