// Етап Е0 (S-05): сторінка перевірки, що система працює наскрізь. Без дизайну — він буде пізніше.
import { useEffect, useState } from 'react';
import { fetchWithWake, getJson, sha256Hex, type WakeProgress } from './api';

type Health = {
  status: string;
  version: string;
  env: string;
  started_at: string;
  uptime_s: number;
  db: { status: string; latency_ms?: number; server_version?: string; error?: string };
};

type Check = { ok: boolean | null; text: string };
const pending: Check = { ok: null, text: 'не перевірено' };

function Mark({ ok }: { ok: boolean | null }) {
  return <span className="mark">{ok === null ? '⏳' : ok ? '✅' : '❌'}</span>;
}

export default function App() {
  const [wake, setWake] = useState<WakeProgress | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthCheck, setHealthCheck] = useState<Check>({ ok: null, text: 'з’єднання з сервером…' });
  const [cookie, setCookie] = useState<Check>(pending);
  const [headers, setHeaders] = useState<unknown>(null);
  const [upload, setUpload] = useState<Check>(pending);

  function recheckHealth() {
    setHealth(null);
    setHealthCheck({ ok: null, text: 'з’єднання з сервером…' });
    void checkHealth();
  }

  async function checkHealth() {
    const started = Date.now();
    try {
      const res = await fetchWithWake('/api/health', { onProgress: setWake });
      const body = (await res.json()) as Health;
      setHealth(body);
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      setHealthCheck({ ok: res.ok, text: `сервер відповів за ${seconds} с` });
    } catch (err) {
      setHealthCheck({ ok: false, text: `сервер недоступний: ${String(err)}` });
    } finally {
      setWake(null);
    }
  }

  async function checkCookie() {
    setCookie(pending);
    try {
      const { set } = await getJson<{ set: string }>('/api/diag/cookie', { method: 'POST' });
      const got = await getJson<{ received: boolean; value: string | null }>('/api/diag/cookie');
      setCookie(
        got.received && got.value === set
          ? { ok: true, text: `cookie встановлено й повернуто (${set})` }
          : { ok: false, text: `cookie не повернулося: ${JSON.stringify(got)}` },
      );
    } catch (err) {
      setCookie({ ok: false, text: String(err) });
    }
  }

  async function checkHeaders() {
    try {
      setHeaders(await getJson('/api/diag/request'));
    } catch (err) {
      setHeaders(String(err));
    }
  }

  async function sendFile(file: Blob, name: string) {
    setUpload({ ok: null, text: `надсилаю ${name} (${(file.size / 1024 / 1024).toFixed(2)} МБ)…` });
    const started = Date.now();
    try {
      const localHash = await sha256Hex(await file.arrayBuffer());
      const form = new FormData();
      form.append('file', file, name);
      const res = await getJson<{ size: number; sha256: string }>('/api/diag/upload', {
        method: 'POST',
        body: form,
      });
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      const same = res.sha256 === localHash && res.size === file.size;
      setUpload({
        ok: same,
        text: same
          ? `файл дійшов цілим: ${res.size} байт за ${seconds} с, SHA-256 збігається`
          : `файл пошкоджено: сервер отримав ${res.size} байт, хеш ${res.sha256}`,
      });
    } catch (err) {
      const rejected = String(err).includes('FILE_TOO_LARGE');
      setUpload({
        ok: rejected && file.size > 5 * 1024 * 1024,
        text: rejected ? `${name}: сервер відмовив — файл понад 5 МБ (FILE_TOO_LARGE)` : `${name}: ${String(err)}`,
      });
    }
  }

  function sendGenerated(bytes: number) {
    const data = new Uint8Array(bytes);
    for (let i = 0; i < bytes; i++) data[i] = 32 + (i % 90);
    void sendFile(new Blob([data], { type: 'text/csv' }), `test_${bytes}.csv`);
  }

  useEffect(() => {
    void checkHealth();
  }, []);

  return (
    <main>
      <h1>GEDH — перевірка системи (етап Е0)</h1>
      <p>
        Сторінка перевіряє ланцюжок браузер → Vercel → Render → Neon. Дизайн сайту буде пізніше.
      </p>

      <section>
        <h2>
          <Mark ok={healthCheck.ok} /> 1. Сервер і база даних
        </h2>
        <p>{healthCheck.text}</p>
        {wake && (
          <p className="warn">
            Сервер прокидається (безкоштовний тариф засинає після 15 хв простою): спроба {wake.attempt}, минуло{' '}
            {Math.round(wake.elapsedMs / 1000)} с…
          </p>
        )}
        {health && (
          <ul>
            <li>
              версія API: {health.version}, середовище: {health.env}
            </li>
            <li>
              сервер працює {health.uptime_s} с (мале значення = щойно був холодний старт)
            </li>
            <li>
              <Mark ok={health.db.status === 'ok'} /> база даних: {health.db.status}
              {health.db.server_version && `, PostgreSQL ${health.db.server_version}`}
              {health.db.latency_ms !== undefined && `, ${health.db.latency_ms} мс`}
              {health.db.error && `, помилка ${health.db.error}`}
            </li>
          </ul>
        )}
        <button onClick={recheckHealth}>Перевірити ще раз</button>
      </section>

      <section>
        <h2>
          <Mark ok={cookie.ok} /> 2. Cookie через проксі
        </h2>
        <p>Майбутній токен оновлення зберігатиметься в httpOnly cookie — він має доходити до API.</p>
        <p>{cookie.text}</p>
        <button onClick={checkCookie}>Перевірити cookie</button>
      </section>

      <section>
        <h2>3. Заголовки запиту на сервері</h2>
        <p>Звідси видно, у якому заголовку приходить реальна IP-адреса клієнта.</p>
        <button onClick={checkHeaders}>Показати</button>
        {headers !== null && <pre>{JSON.stringify(headers, null, 2)}</pre>}
      </section>

      <section>
        <h2>
          <Mark ok={upload.ok} /> 4. Завантаження файлу
        </h2>
        <p>Набір даних може мати до 5 МБ — файл має пройти через проксі без змін.</p>
        <p>{upload.text}</p>
        <button onClick={() => sendGenerated(100 * 1024)}>100 КБ</button>{' '}
        <button onClick={() => sendGenerated(5 * 1024 * 1024 - 1024)}>майже 5 МБ</button>{' '}
        <button onClick={() => sendGenerated(5 * 1024 * 1024 + 1)}>понад 5 МБ (має бути відмова)</button>{' '}
        <label>
          або свій файл:{' '}
          <input type="file" onChange={(e) => e.target.files?.[0] && sendFile(e.target.files[0], e.target.files[0].name)} />
        </label>
      </section>
    </main>
  );
}
