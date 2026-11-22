import { describe, expect, it, vi } from 'vitest';
import { fetchWithWake, sha256Hex } from './api';

const noSleep = () => Promise.resolve();

describe('fetchWithWake', () => {
  it('повторює запит, поки сервер прокидається (503, мережева помилка)', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }));
    const res = await fetchWithWake('/api/health', { fetchFn, sleep: noSleep });
    expect(res.status).toBe(200);
    expect(fetchFn).toHaveBeenCalledTimes(3);
  });

  it('не повторює звичайну помилку застосунку (404)', async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    const res = await fetchWithWake('/api/x', { fetchFn, sleep: noSleep });
    expect(res.status).toBe(404);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('здається після вичерпання спроб', async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response(null, { status: 502 }));
    await expect(fetchWithWake('/api/health', { fetchFn, sleep: noSleep, attempts: 3 })).rejects.toThrow(
      'HTTP 502',
    );
    expect(fetchFn).toHaveBeenCalledTimes(3);
  });
});

describe('sha256Hex', () => {
  it('рахує хеш як backend', async () => {
    const data = new TextEncoder().encode('abc').buffer as ArrayBuffer;
    expect(await sha256Hex(data)).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });
});
