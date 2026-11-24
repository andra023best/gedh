// Журнал аудиту для DG-офіцера (UC-15; FR-31, FR-32, FR-40; DG-17 – DG-19).
// Кожен перегляд, перевірка цілісності й експорт самі записуються в журнал (постумова UC-15).
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { AUDIT_ACTION_UK, AUDIT_RESULT_UK, OBJECT_TYPE_UK } from '../lib/auditLabels';
import { blobFetch } from '../lib/download';
import { errorText, ROLE_UK } from '../lib/messages';
import { api } from '../lib/session';

type AuditEvent = {
  seq: number;
  occurred_at: string;
  actor_id: string | null;
  actor_role: string | null;
  actor_label: string | null;
  on_behalf_of_id: string | null;
  organization_label: string | null;
  action: string;
  object_type: string;
  object_id: string;
  result: string;
  request_id: string;
  details: string;
  prev_hash: string;
  hash: string;
  hash_valid?: boolean;
};
type Page = { items: AuditEvent[]; next_before_seq: number | null };
type Integrity = {
  ok: boolean;
  checked: number;
  first_broken_seq: number | null;
  last_seq: number;
  last_hash: string;
  checked_at: string;
};
type Named = { code: string; name_ua: string };
type Org = { id: string; name: string };
type Ds = { id: string; title_ua: string; kind: string };

type Filters = Record<string, string>;
const EMPTY: Filters = {
  actor_email: '',
  organization_id: '',
  country: '',
  dataset_id: '',
  action: '',
  result: '',
  from: '',
  to: '',
};

function query(f: Filters, extra: Record<string, string> = {}) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...f, ...extra })) {
    if (!v) continue;
    // дати з форми — межі доби за UTC
    if (k === 'from') p.set(k, `${v}T00:00:00Z`);
    else if (k === 'to') p.set(k, `${v}T23:59:59Z`);
    else p.set(k, v);
  }
  return p.toString();
}

const t = (s: string) => new Date(s).toLocaleString('uk-UA');
const short = (s: string) => (s.length > 12 ? `${s.slice(0, 8)}…` : s);

function Details({ seq, onClose }: { seq: number; onClose: () => void }) {
  const e = useQuery({
    queryKey: ['audit-event', seq],
    queryFn: () => api<AuditEvent>(`/audit/events/${seq}`),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.scrollIntoView({ block: 'start' }), [seq, e.data]);
  if (e.error) return <p className="error">{errorText(e.error)}</p>;
  if (!e.data) return <p>Завантаження…</p>;
  const ev = e.data;
  let details: unknown = ev.details;
  try {
    details = JSON.parse(ev.details);
  } catch {
    /* деталі — не JSON: показуємо як є */
  }
  return (
    <div className="card" ref={box}>
      <h2>
        Запис № {ev.seq}{' '}
        <button onClick={onClose} aria-label="Закрити">
          ×
        </button>
      </h2>
      <dl className="kv">
        <dt>Час (UTC)</dt>
        <dd>{ev.occurred_at}</dd>
        <dt>Виконавець</dt>
        <dd>
          {ev.actor_label ?? '—'} {ev.actor_role && `· ${ROLE_UK[ev.actor_role] ?? ev.actor_role}`}
          {ev.actor_id && <div className="hint">ідентифікатор: {ev.actor_id}</div>}
        </dd>
        <dt>Від імені організації</dt>
        <dd>{ev.organization_label ?? '—'}</dd>
        <dt>Дія</dt>
        <dd>
          {AUDIT_ACTION_UK[ev.action] ?? ev.action} <span className="hint">({ev.action})</span>
        </dd>
        <dt>Об'єкт</dt>
        <dd>
          {OBJECT_TYPE_UK[ev.object_type] ?? ev.object_type}: <code>{ev.object_id}</code>
        </dd>
        <dt>Результат</dt>
        <dd>{AUDIT_RESULT_UK[ev.result] ?? ev.result}</dd>
        <dt>Ідентифікатор запиту</dt>
        <dd>
          <code>{ev.request_id}</code>
        </dd>
        <dt>Деталі</dt>
        <dd>
          <pre>{JSON.stringify(details, null, 2)}</pre>
        </dd>
        <dt>Хеш попереднього запису</dt>
        <dd>
          <code className="hash">{ev.prev_hash}</code>
        </dd>
        <dt>Хеш запису (SHA-256)</dt>
        <dd>
          <code className="hash">{ev.hash}</code>
        </dd>
        <dt>Перевірка хешу</dt>
        <dd className={ev.hash_valid ? 'ok' : 'error'}>
          {ev.hash_valid ? 'хеш відповідає вмісту й попередньому запису' : 'хеш НЕ відповідає — запис змінено'}
        </dd>
      </dl>
      <p className="hint">У записі журналу немає ПІБ, e-mail та IP-адрес — лише ідентифікатори (D-40).</p>
    </div>
  );
}

export default function AuditPage() {
  const [form, setForm] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [older, setOlder] = useState<Page[]>([]);
  const [nonce, setNonce] = useState(0); // «Показати» з тими самими фільтрами теж оновлює список
  const [open, setOpen] = useState<number | null>(null);
  const [integrity, setIntegrity] = useState<Integrity | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const countries = useQuery({ queryKey: ['countries'], queryFn: () => api<Named[]>('/countries') });
  const orgs = useQuery({ queryKey: ['organizations'], queryFn: () => api<Org[]>('/organizations') });
  const datasets = useQuery({ queryKey: ['catalog'], queryFn: () => api<Ds[]>('/catalog/datasets') });

  const first = useQuery({
    queryKey: ['audit', applied, nonce],
    queryFn: () => api<Page>(`/audit/events?${query(applied, { limit: '50' })}`),
    // без автоматичних повторних запитів: кожен перегляд — запис у журналі
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  // старіші сторінки дозавантажуються по одній (кожен запит — окремий запис AUDIT_VIEW)
  const items = [...(first.data?.items ?? []), ...older.flatMap((p) => p.items)];
  const next = older.length ? older[older.length - 1].next_before_seq : first.data?.next_before_seq;
  const loadOlder = async (before: number) => {
    setBusy(true);
    try {
      const p = await api<Page>(`/audit/events?${query(applied, { limit: '50', before_seq: String(before) })}`);
      setOlder([...older, p]);
    } catch (err) {
      setMsg({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  const set = (k: string) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const apply = (e: FormEvent) => {
    e.preventDefault();
    setOlder([]);
    setOpen(null);
    setApplied({ ...form });
    setNonce(nonce + 1);
  };

  const verify = async () => {
    setBusy(true);
    setMsg(null);
    try {
      setIntegrity(await api<Integrity>('/audit/integrity'));
    } catch (err) {
      setMsg({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };
  const exportCsv = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const h = await blobFetch(`/audit/export?${query(applied)}`);
      setMsg({ ok: true, text: `Файл збережено. SHA-256: ${h.get('x-content-sha256') ?? '—'} (записано в журнал).` });
    } catch (err) {
      setMsg({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <h1>Журнал аудиту</h1>
      <p className="hint">
        Журнал лише доповнюється: змінити чи видалити запис не може ні застосунок, ні адміністратор (DG-19). Кожен запис
        містить хеш попереднього. Ваш перегляд, перевірка й експорт також записуються.
      </p>

      <form className="filters" onSubmit={apply}>
        <label>
          Користувач (e-mail)
          <input type="email" value={form.actor_email} onChange={set('actor_email')} />
        </label>
        <label>
          Організація
          <select value={form.organization_id} onChange={set('organization_id')}>
            <option value="">усі</option>
            {orgs.data?.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Країна
          <select value={form.country} onChange={set('country')}>
            <option value="">усі</option>
            {countries.data?.map((c) => (
              <option key={c.code} value={c.code}>
                {c.code} — {c.name_ua}
              </option>
            ))}
          </select>
        </label>
        <label>
          Набір даних
          <select value={form.dataset_id} onChange={set('dataset_id')}>
            <option value="">усі</option>
            {datasets.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title_ua}
                {d.kind === 'RESTRICTED' ? ' (закритий)' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          Дія
          <select value={form.action} onChange={set('action')}>
            <option value="">усі</option>
            {Object.entries(AUDIT_ACTION_UK).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Результат
          <select value={form.result} onChange={set('result')}>
            <option value="">усі</option>
            {Object.entries(AUDIT_RESULT_UK).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Від
          <input type="date" value={form.from} onChange={set('from')} />
        </label>
        <label>
          До
          <input type="date" value={form.to} onChange={set('to')} />
        </label>
        <div>
          <button type="submit">Показати</button>{' '}
          <button
            type="button"
            onClick={() => {
              setForm(EMPTY);
              setApplied(EMPTY);
              setOlder([]);
            }}
          >
            Скинути
          </button>
        </div>
      </form>

      <p>
        <button onClick={() => void verify()} disabled={busy}>
          Перевірити цілісність журналу
        </button>{' '}
        <button onClick={() => void exportCsv()} disabled={busy}>
          Експорт вибірки в CSV
        </button>
      </p>
      {integrity && (
        <p className={integrity.ok ? 'ok' : 'error'}>
          {integrity.ok
            ? `Цілісність підтверджено: перевірено ${integrity.checked} записів (останній № ${integrity.last_seq}).`
            : `Ланцюжок порушено, починаючи із запису № ${integrity.first_broken_seq}.`}{' '}
          <span className="hint">Хеш останнього запису: {short(integrity.last_hash)}</span>
        </p>
      )}
      {msg && <p className={msg.ok ? 'ok' : 'error'}>{msg.text}</p>}
      {applied.dataset_id && (
        <p>
          <Link to={`/datasets/${applied.dataset_id}/lineage`}>Походження цього набору (UC-15 AC2)</Link>
        </p>
      )}

      {open !== null && <Details seq={open} onClose={() => setOpen(null)} />}

      {first.error && <p className="error">{errorText(first.error)}</p>}
      {first.data && items.length === 0 && <p>Записів за цими умовами немає.</p>}
      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>№</th>
              <th>Час</th>
              <th>Виконавець</th>
              <th>Організація</th>
              <th>Дія</th>
              <th>Об'єкт</th>
              <th>Результат</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.seq} className={e.result !== 'SUCCESS' ? 'denied' : undefined}>
                <td>{e.seq}</td>
                <td>{t(e.occurred_at)}</td>
                <td>
                  {e.actor_label ?? '—'}
                  {e.actor_role && <div className="hint">{ROLE_UK[e.actor_role] ?? e.actor_role}</div>}
                </td>
                <td>{e.organization_label ?? ''}</td>
                <td>{AUDIT_ACTION_UK[e.action] ?? e.action}</td>
                <td>
                  {OBJECT_TYPE_UK[e.object_type] ?? e.object_type} <code>{short(e.object_id)}</code>
                </td>
                <td>{AUDIT_RESULT_UK[e.result] ?? e.result}</td>
                <td>
                  <button onClick={() => setOpen(e.seq)}>Деталі</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {next && (
        <p>
          <button onClick={() => void loadOlder(next)} disabled={busy}>
            Показати старіші
          </button>
        </p>
      )}
    </section>
  );
}
