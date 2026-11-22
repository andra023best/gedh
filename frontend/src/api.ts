// Клієнт API. Усі запити йдуть на той самий домен (/api/*): локально їх проксіює Vite,
// на Vercel — rewrites на Render (D-39), тож CORS не потрібен, а cookie лишаються host-only.

export type WakeProgress = { attempt: number; elapsedMs: number };

type WaitOptions = {
  attempts?: number;
  timeoutMs?: number;
  delayMs?: number;
  onProgress?: (p: WakeProgress) => void;
  fetchFn?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
};

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Чекає, доки сервер відповість. Render Free засинає після 15 хв простою
 * й прокидається 30–60 с, тому перший запит повторюємо (нотатки для розробки, розд. 6).
 */
export async function fetchWithWake(url: string, opts: WaitOptions = {}): Promise<Response> {
  const {
    attempts = 12,
    timeoutMs = 10_000,
    delayMs = 3_000,
    onProgress,
    fetchFn = fetch,
    sleep = defaultSleep,
  } = opts;
  const started = Date.now();
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    onProgress?.({ attempt, elapsedMs: Date.now() - started });
    try {
      const res = await fetchFn(url, { signal: AbortSignal.timeout(timeoutMs) });
      // 502/503/504 — проксі ще чекає на сервер, що прокидається.
      if (![502, 503, 504].includes(res.status)) return res;
      lastError = new Error(`HTTP ${res.status}`);
    } catch (err) {
      lastError = err;
    }
    if (attempt < attempts) await sleep(delayMs);
  }
  throw lastError instanceof Error ? lastError : new Error('SERVER_UNAVAILABLE');
}

export async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: 'same-origin', ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.detail ?? `HTTP ${res.status}`);
  return body as T;
}

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
