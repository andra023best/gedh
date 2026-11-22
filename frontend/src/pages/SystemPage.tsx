// Р•С‚Р°Рї Р•0 (S-05): СЃС‚РѕСЂС–РЅРєР° РїРµСЂРµРІС–СЂРєРё, С‰Рѕ СЃРёСЃС‚РµРјР° РїСЂР°С†СЋС” РЅР°СЃРєСЂС–Р·СЊ. Р‘РµР· РґРёР·Р°Р№РЅСѓ вЂ” РІС–РЅ Р±СѓРґРµ РїС–Р·РЅС–С€Рµ.
import { useEffect, useState } from 'react';
import { fetchWithWake, getJson, sha256Hex, type WakeProgress } from '../api';

type Health = {
  status: string;
  version: string;
  env: string;
  started_at: string;
  uptime_s: number;
  db: { status: string; latency_ms?: number; server_version?: string; error?: string };
};

type Check = { ok: boolean | null; text: string };
const pending: Check = { ok: null, text: 'РЅРµ РїРµСЂРµРІС–СЂРµРЅРѕ' };

function Mark({ ok }: { ok: boolean | null }) {
  return <span className="mark">{ok === null ? 'вЏі' : ok ? 'вњ…' : 'вќЊ'}</span>;
}

export default function SystemPage() {
  const [wake, setWake] = useState<WakeProgress | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthCheck, setHealthCheck] = useState<Check>({ ok: null, text: 'Р·вЂ™С”РґРЅР°РЅРЅСЏ Р· СЃРµСЂРІРµСЂРѕРјвЂ¦' });
  const [cookie, setCookie] = useState<Check>(pending);
  const [headers, setHeaders] = useState<unknown>(null);
  const [upload, setUpload] = useState<Check>(pending);

  function recheckHealth() {
    setHealth(null);
    setHealthCheck({ ok: null, text: 'Р·вЂ™С”РґРЅР°РЅРЅСЏ Р· СЃРµСЂРІРµСЂРѕРјвЂ¦' });
    void checkHealth();
  }

  async function checkHealth() {
    const started = Date.now();
    try {
      const res = await fetchWithWake('/api/health', { onProgress: setWake });
      const body = (await res.json()) as Health;
      setHealth(body);
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      setHealthCheck({ ok: res.ok, text: `СЃРµСЂРІРµСЂ РІС–РґРїРѕРІС–РІ Р·Р° ${seconds} СЃ` });
    } catch (err) {
      setHealthCheck({ ok: false, text: `СЃРµСЂРІРµСЂ РЅРµРґРѕСЃС‚СѓРїРЅРёР№: ${String(err)}` });
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
          ? { ok: true, text: `cookie РІСЃС‚Р°РЅРѕРІР»РµРЅРѕ Р№ РїРѕРІРµСЂРЅСѓС‚Рѕ (${set})` }
          : { ok: false, text: `cookie РЅРµ РїРѕРІРµСЂРЅСѓР»РѕСЃСЏ: ${JSON.stringify(got)}` },
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
    setUpload({ ok: null, text: `РЅР°РґСЃРёР»Р°СЋ ${name} (${(file.size / 1024 / 1024).toFixed(2)} РњР‘)вЂ¦` });
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
          ? `С„Р°Р№Р» РґС–Р№С€РѕРІ С†С–Р»РёРј: ${res.size} Р±Р°Р№С‚ Р·Р° ${seconds} СЃ, SHA-256 Р·Р±С–РіР°С”С‚СЊСЃСЏ`
          : `С„Р°Р№Р» РїРѕС€РєРѕРґР¶РµРЅРѕ: СЃРµСЂРІРµСЂ РѕС‚СЂРёРјР°РІ ${res.size} Р±Р°Р№С‚, С…РµС€ ${res.sha256}`,
      });
    } catch (err) {
      const rejected = String(err).includes('FILE_TOO_LARGE');
      setUpload({
        ok: rejected && file.size > 5 * 1024 * 1024,
        text: rejected ? `${name}: СЃРµСЂРІРµСЂ РІС–РґРјРѕРІРёРІ вЂ” С„Р°Р№Р» РїРѕРЅР°Рґ 5 РњР‘ (FILE_TOO_LARGE)` : `${name}: ${String(err)}`,
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
      <h1>GEDH вЂ” РїРµСЂРµРІС–СЂРєР° СЃРёСЃС‚РµРјРё (РµС‚Р°Рї Р•0)</h1>
      <p>
        РЎС‚РѕСЂС–РЅРєР° РїРµСЂРµРІС–СЂСЏС” Р»Р°РЅС†СЋР¶РѕРє Р±СЂР°СѓР·РµСЂ в†’ Vercel в†’ Render в†’ Neon. Р”РёР·Р°Р№РЅ СЃР°Р№С‚Сѓ Р±СѓРґРµ РїС–Р·РЅС–С€Рµ.
      </p>

      <section>
        <h2>
          <Mark ok={healthCheck.ok} /> 1. РЎРµСЂРІРµСЂ С– Р±Р°Р·Р° РґР°РЅРёС…
        </h2>
        <p>{healthCheck.text}</p>
        {wake && (
          <p className="warn">
            РЎРµСЂРІРµСЂ РїСЂРѕРєРёРґР°С”С‚СЊСЃСЏ (Р±РµР·РєРѕС€С‚РѕРІРЅРёР№ С‚Р°СЂРёС„ Р·Р°СЃРёРЅР°С” РїС–СЃР»СЏ 15 С…РІ РїСЂРѕСЃС‚РѕСЋ): СЃРїСЂРѕР±Р° {wake.attempt}, РјРёРЅСѓР»Рѕ{' '}
            {Math.round(wake.elapsedMs / 1000)} СЃвЂ¦
          </p>
        )}
        {health && (
          <ul>
            <li>
              РІРµСЂСЃС–СЏ API: {health.version}, СЃРµСЂРµРґРѕРІРёС‰Рµ: {health.env}
            </li>
            <li>
              СЃРµСЂРІРµСЂ РїСЂР°С†СЋС” {health.uptime_s} СЃ (РјР°Р»Рµ Р·РЅР°С‡РµРЅРЅСЏ = С‰РѕР№РЅРѕ Р±СѓРІ С…РѕР»РѕРґРЅРёР№ СЃС‚Р°СЂС‚)
            </li>
            <li>
              <Mark ok={health.db.status === 'ok'} /> Р±Р°Р·Р° РґР°РЅРёС…: {health.db.status}
              {health.db.server_version && `, PostgreSQL ${health.db.server_version}`}
              {health.db.latency_ms !== undefined && `, ${health.db.latency_ms} РјСЃ`}
              {health.db.error && `, РїРѕРјРёР»РєР° ${health.db.error}`}
            </li>
          </ul>
        )}
        <button onClick={recheckHealth}>РџРµСЂРµРІС–СЂРёС‚Рё С‰Рµ СЂР°Р·</button>
      </section>

      <section>
        <h2>
          <Mark ok={cookie.ok} /> 2. Cookie С‡РµСЂРµР· РїСЂРѕРєСЃС–
        </h2>
        <p>РњР°Р№Р±СѓС‚РЅС–Р№ С‚РѕРєРµРЅ РѕРЅРѕРІР»РµРЅРЅСЏ Р·Р±РµСЂС–РіР°С‚РёРјРµС‚СЊСЃСЏ РІ httpOnly cookie вЂ” РІС–РЅ РјР°С” РґРѕС…РѕРґРёС‚Рё РґРѕ API.</p>
        <p>{cookie.text}</p>
        <button onClick={checkCookie}>РџРµСЂРµРІС–СЂРёС‚Рё cookie</button>
      </section>

      <section>
        <h2>3. Р—Р°РіРѕР»РѕРІРєРё Р·Р°РїРёС‚Сѓ РЅР° СЃРµСЂРІРµСЂС–</h2>
        <p>Р—РІС–РґСЃРё РІРёРґРЅРѕ, Сѓ СЏРєРѕРјСѓ Р·Р°РіРѕР»РѕРІРєСѓ РїСЂРёС…РѕРґРёС‚СЊ СЂРµР°Р»СЊРЅР° IP-Р°РґСЂРµСЃР° РєР»С–С”РЅС‚Р°.</p>
        <button onClick={checkHeaders}>РџРѕРєР°Р·Р°С‚Рё</button>
        {headers !== null && <pre>{JSON.stringify(headers, null, 2)}</pre>}
      </section>

      <section>
        <h2>
          <Mark ok={upload.ok} /> 4. Р—Р°РІР°РЅС‚Р°Р¶РµРЅРЅСЏ С„Р°Р№Р»Сѓ
        </h2>
        <p>РќР°Р±С–СЂ РґР°РЅРёС… РјРѕР¶Рµ РјР°С‚Рё РґРѕ 5 РњР‘ вЂ” С„Р°Р№Р» РјР°С” РїСЂРѕР№С‚Рё С‡РµСЂРµР· РїСЂРѕРєСЃС– Р±РµР· Р·РјС–РЅ.</p>
        <p>{upload.text}</p>
        <button onClick={() => sendGenerated(100 * 1024)}>100 РљР‘</button>{' '}
        <button onClick={() => sendGenerated(5 * 1024 * 1024 - 1024)}>РјР°Р№Р¶Рµ 5 РњР‘</button>{' '}
        <button onClick={() => sendGenerated(5 * 1024 * 1024 + 1)}>РїРѕРЅР°Рґ 5 РњР‘ (РјР°С” Р±СѓС‚Рё РІС–РґРјРѕРІР°)</button>{' '}
        <label>
          Р°Р±Рѕ СЃРІС–Р№ С„Р°Р№Р»:{' '}
          <input type="file" onChange={(e) => e.target.files?.[0] && sendFile(e.target.files[0], e.target.files[0].name)} />
        </label>
      </section>
    </main>
  );
}
