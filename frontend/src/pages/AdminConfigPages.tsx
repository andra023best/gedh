// Адміністратор: конектори імпорту (UC-20, FR-17) і довідники (FR-43). Кожна зміна — у журнал аудиту зі старим і новим значенням.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { errorText } from '../lib/messages';
import { api, ApiError, post } from '../lib/session';

type Run = {
  id: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  records: number;
  request_url: string;
  raw_sha256: string | null;
  version_id: string | null;
  error: string | null;
};
type Connector = {
  id: string;
  name: string;
  indicator: string;
  source_system: string;
  schedule: string;
  is_enabled: boolean;
  next_run: string | null;
  dataset_title: string | null;
  runs: Run[];
};
const RUN_UK: Record<string, string> = {
  RUNNING: 'виконується',
  SUCCEEDED: 'успішно',
  FAILED: 'помилка джерела',
  REJECTED_BY_DQ: 'відхилено перевіркою якості',
};
const dt = (s: string | null) => (s ? new Date(s).toLocaleString('uk-UA') : '—');
const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });

function errorWithReason(err: unknown): string {
  const reason = err instanceof ApiError && typeof err.body.reason === 'string' ? ` ${err.body.reason}` : '';
  return errorText(err) + reason;
}

// ------------------------------------------------------------------------------------------------ конектори
function ConnectorRow({ c, onChanged }: { c: Connector; onChanged: (msg: string, ok?: boolean) => void }) {
  const [schedule, setSchedule] = useState(c.schedule);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      onChanged(ok);
    } catch (err) {
      onChanged(`${c.name}: ${errorWithReason(err)}`, false);
    } finally {
      setBusy(false);
    }
  }
  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    await act(async () => {
      const run = await api<Run>(`/admin/connectors/${c.id}/upload`, { method: 'POST', body: form });
      if (run.status !== 'SUCCEEDED') throw new Error(RUN_UK[run.status] ?? run.status);
    }, `${c.name}: файл імпортовано (резервний імпорт, UC-20 AC4).`);
  }

  const last = c.runs[0];
  return (
    <>
      <tr className={c.is_enabled ? undefined : 'denied'}>
        <td>
          <strong>{c.name}</strong>
          <div className="hint">{c.dataset_title}</div>
        </td>
        <td>
          <input value={schedule} onChange={(e) => setSchedule(e.target.value)} size={14} aria-label="Розклад cron" />{' '}
          {schedule !== c.schedule && (
            <button disabled={busy} onClick={() => void act(() => patch(`/admin/connectors/${c.id}`, { schedule }), `${c.name}: розклад збережено.`)}>
              Зберегти
            </button>
          )}
          <div className="hint">наступний запуск: {c.next_run ? new Date(c.next_run).toLocaleDateString('uk-UA') : '—'}</div>
        </td>
        <td>
          <label>
            <input
              type="checkbox"
              checked={c.is_enabled}
              disabled={busy}
              onChange={(e) =>
                void act(
                  () => patch(`/admin/connectors/${c.id}`, { is_enabled: e.target.checked }),
                  `${c.name}: ${e.target.checked ? 'увімкнено' : 'вимкнено'}.`,
                )
              }
            />{' '}
            увімкнено
          </label>
        </td>
        <td>
          {last ? (
            <>
              {RUN_UK[last.status] ?? last.status}
              <div className="hint">
                {dt(last.started_at)} · {last.records} записів · {last.trigger === 'SCHEDULE' ? 'за розкладом' : 'вручну'}
              </div>
            </>
          ) : (
            'ще не запускався'
          )}
        </td>
        <td>
          <button disabled={busy} onClick={() => void act(() => post(`/admin/connectors/${c.id}/run`), `${c.name}: запуск розпочато, оновіть сторінку за хвилину.`)}>
            Запустити
          </button>{' '}
          <button onClick={() => setOpen(!open)}>{open ? 'Сховати' : 'Журнал і CSV'}</button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5}>
            <form onSubmit={(e) => void upload(e)} className="inline-form">
              Резервний імпорт з офіційного CSV-експорту джерела (World Bank — як є; IMF і BIS — колонки{' '}
              <code>country,year,value</code>): <input type="file" name="file" accept=".csv,text/csv" required />{' '}
              <button type="submit" disabled={busy}>
                Імпортувати файл
              </button>
            </form>
            <table>
              <thead>
                <tr>
                  <th>Початок</th>
                  <th>Як</th>
                  <th>Результат</th>
                  <th>Записів</th>
                  <th>Джерело запиту</th>
                  <th>SHA-256 сирих даних</th>
                </tr>
              </thead>
              <tbody>
                {c.runs.map((r) => (
                  <tr key={r.id}>
                    <td>{dt(r.started_at)}</td>
                    <td>{r.trigger === 'SCHEDULE' ? 'розклад' : 'вручну'}</td>
                    <td>
                      {RUN_UK[r.status] ?? r.status}
                      {r.version_id ? ' · нова версія' : r.status === 'SUCCEEDED' ? ' · без змін' : ''}
                      {r.error && <div className="hint">{r.error}</div>}
                    </td>
                    <td>{r.records}</td>
                    <td className="hint">{r.request_url.length > 60 ? `${r.request_url.slice(0, 60)}…` : r.request_url}</td>
                    <td>
                      <code>{r.raw_sha256 ? `${r.raw_sha256.slice(0, 12)}…` : '—'}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

export function ConnectorsPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ['connectors'], queryFn: () => api<Connector[]>('/admin/connectors') });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const changed = (text: string, ok = true) => {
    setMsg({ ok, text });
    void qc.invalidateQueries({ queryKey: ['connectors'] });
  };
  return (
    <section>
      <h1>Конектори імпорту</h1>
      <p className="hint">
        Щодня о 03:00 UTC планувальник запускає конектори, чий розклад припадає на цей день. Розклад — вираз cron із 5
        полів «хвилина година день місяць день_тижня»: <code>0 3 * * 1</code> — щопонеділка, <code>0 3 1 * *</code> —
        першого числа. Неправильний вираз не зберігається (UC-20 EX1). Нова версія з'являється лише тоді, коли джерело
        змінило значення.
      </p>
      {msg && <p className={msg.ok ? 'ok' : 'error'}>{msg.text}</p>}
      {list.error && <p className="error">{errorText(list.error)}</p>}
      {list.data && (
        <table>
          <thead>
            <tr>
              <th>Конектор</th>
              <th>Розклад</th>
              <th>Стан</th>
              <th>Останній запуск</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((c) => (
              <ConnectorRow key={c.id + c.schedule + String(c.is_enabled)} c={c} onChanged={changed} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ довідники
type Country = { code: string; name_ua: string; name_en: string; iso2: string | null; iso_numeric: string | null; is_active: boolean };
type Indicator = {
  code: string;
  name_ua: string;
  name_en: string;
  definition: string;
  unit: string;
  scale: string;
  frequency: string;
  is_active: boolean;
  hard_min: number | null;
  hard_max: number | null;
  typical_min: number | null;
  typical_max: number | null;
};
type Category = { code: string; name_ua: string; name_en: string };

function formObject(e: FormEvent<HTMLFormElement>, numeric: string[] = []): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of new FormData(e.currentTarget).entries()) {
    const s = String(v).trim();
    if (s === '') continue;
    out[k] = numeric.includes(k) ? Number(s) : s;
  }
  return out;
}

export function ReferencePage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<'countries' | 'indicators' | 'categories'>('countries');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const countries = useQuery({ queryKey: ['countries'], queryFn: () => api<Country[]>('/countries') });
  const indicators = useQuery({ queryKey: ['admin-indicators'], queryFn: () => api<Indicator[]>('/admin/indicators') });
  const categories = useQuery({ queryKey: ['categories'], queryFn: () => api<Category[]>('/categories') });
  const [editing, setEditing] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, ok: string, form?: HTMLFormElement) {
    setMsg(null);
    try {
      await fn();
      setMsg({ ok: true, text: ok });
      form?.reset();
      setEditing(null);
      for (const k of ['countries', 'admin-indicators', 'categories', 'indicators']) void qc.invalidateQueries({ queryKey: [k] });
    } catch (err) {
      setMsg({ ok: false, text: errorWithReason(err) });
    }
  }
  const NUM = ['hard_min', 'hard_max', 'typical_min', 'typical_max'];

  return (
    <section>
      <h1>Довідники</h1>
      <p className="hint">
        Зміни записуються в журнал аудиту зі старим і новим значенням (UC-20). Показник, для якого є дані, не видаляється —
        лише деактивується (EX2). Організації — на сторінці «Заявки на реєстрацію».
      </p>
      <p>
        <button onClick={() => setTab('countries')} disabled={tab === 'countries'}>
          Країни
        </button>{' '}
        <button onClick={() => setTab('indicators')} disabled={tab === 'indicators'}>
          Показники
        </button>{' '}
        <button onClick={() => setTab('categories')} disabled={tab === 'categories'}>
          Категорії закритих даних
        </button>
      </p>
      {msg && <p className={msg.ok ? 'ok' : 'error'}>{msg.text}</p>}

      {tab === 'countries' && (
        <>
          <form
            className="filters"
            onSubmit={(e) => {
              e.preventDefault();
              const f = e.currentTarget;
              void run(() => post('/admin/countries', formObject(e)), 'Країну додано. Після наступного імпорту вона стане зеленою на карті.', f);
            }}
          >
            <label>
              Код ISO 3166-1 alpha-3
              <input name="code" required pattern="[A-Z]{3}" placeholder="MDA" />
            </label>
            <label>
              Назва українською
              <input name="name_ua" required />
            </label>
            <label>
              Назва англійською
              <input name="name_en" required />
            </label>
            <label>
              alpha-2 (для BIS)
              <input name="iso2" pattern="[A-Z]{2}" placeholder="MD" />
            </label>
            <label>
              Числовий код (для карти)
              <input name="iso_numeric" pattern="[0-9]{3}" placeholder="498" />
            </label>
            <div>
              <button type="submit">Додати країну</button>
            </div>
          </form>
          <table>
            <thead>
              <tr>
                <th>Код</th>
                <th>Назва</th>
                <th>alpha-2 / числовий</th>
                <th>Активна</th>
              </tr>
            </thead>
            <tbody>
              {countries.data?.map((c) => (
                <tr key={c.code} className={c.is_active ? undefined : 'denied'}>
                  <td>{c.code}</td>
                  <td>
                    {c.name_ua} <span className="hint">{c.name_en}</span>
                  </td>
                  <td>
                    {c.iso2 ?? '—'} / {c.iso_numeric ?? '—'}
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={c.is_active}
                      aria-label={`Активна: ${c.name_ua}`}
                      onChange={(e) =>
                        void run(() => patch(`/admin/countries/${c.code}`, { is_active: e.target.checked }), `${c.name_ua}: збережено.`)
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tab === 'indicators' && (
        <>
          <form
            className="filters"
            onSubmit={(e) => {
              e.preventDefault();
              const f = e.currentTarget;
              void run(() => post('/admin/indicators', formObject(e, NUM)), 'Показник додано.', f);
            }}
          >
            <label>
              Код
              <input name="code" required pattern="[A-Z][A-Z0-9_]{1,39}" placeholder="WAGE_AVG" />
            </label>
            <label>
              Назва українською
              <input name="name_ua" required />
            </label>
            <label>
              Назва англійською
              <input name="name_en" required />
            </label>
            <label>
              Визначення
              <input name="definition" required />
            </label>
            <label>
              Одиниця
              <input name="unit" required placeholder="%" />
            </label>
            <label>
              Масштаб
              <input name="scale" required placeholder="одиниці" />
            </label>
            <label>
              Частота
              <select name="frequency">
                <option value="ANNUAL">щорічно</option>
                <option value="QUARTERLY">щокварталу</option>
                <option value="MONTHLY">щомісяця</option>
              </select>
            </label>
            <label>
              Можливий діапазон, від — до (Q-06)
              <span>
                <input name="hard_min" type="number" step="any" size={6} /> — <input name="hard_max" type="number" step="any" size={6} />
              </span>
            </label>
            <label>
              Типовий діапазон, від — до (Q-07)
              <span>
                <input name="typical_min" type="number" step="any" size={6} /> —{' '}
                <input name="typical_max" type="number" step="any" size={6} />
              </span>
            </label>
            <div>
              <button type="submit">Додати показник</button>
            </div>
          </form>
          <table>
            <thead>
              <tr>
                <th>Код</th>
                <th>Назва й визначення</th>
                <th>Одиниця</th>
                <th>Діапазони Q-06 / Q-07</th>
                <th>Активний</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {indicators.data?.map((i) =>
                editing === i.code ? (
                  <tr key={i.code}>
                    <td colSpan={6}>
                      <form
                        className="filters"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void run(() => patch(`/admin/indicators/${i.code}`, formObject(e, NUM)), `${i.code}: збережено.`);
                        }}
                      >
                        <label>
                          Назва українською
                          <input name="name_ua" defaultValue={i.name_ua} />
                        </label>
                        <label>
                          Назва англійською
                          <input name="name_en" defaultValue={i.name_en} />
                        </label>
                        <label>
                          Визначення
                          <input name="definition" defaultValue={i.definition} />
                        </label>
                        <label>
                          Одиниця
                          <input name="unit" defaultValue={i.unit} />
                        </label>
                        <label>
                          Масштаб
                          <input name="scale" defaultValue={i.scale} />
                        </label>
                        {NUM.map((k) => (
                          <label key={k}>
                            {k}
                            <input name={k} type="number" step="any" defaultValue={i[k as keyof Indicator] as number | undefined ?? ''} />
                          </label>
                        ))}
                        <div>
                          <button type="submit">Зберегти</button> <button type="button" onClick={() => setEditing(null)}>Скасувати</button>
                        </div>
                      </form>
                    </td>
                  </tr>
                ) : (
                  <tr key={i.code} className={i.is_active ? undefined : 'denied'}>
                    <td>
                      <code>{i.code}</code>
                    </td>
                    <td>
                      {i.name_ua}
                      <div className="hint">{i.definition}</div>
                    </td>
                    <td>
                      {i.unit}, {i.scale}
                    </td>
                    <td>
                      {i.hard_min ?? '—'} … {i.hard_max ?? '—'} / {i.typical_min ?? '—'} … {i.typical_max ?? '—'}
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={i.is_active}
                        aria-label={`Активний: ${i.code}`}
                        onChange={(e) => void run(() => patch(`/admin/indicators/${i.code}`, { is_active: e.target.checked }), `${i.code}: збережено.`)}
                      />
                    </td>
                    <td>
                      <button onClick={() => setEditing(i.code)}>Змінити</button>{' '}
                      <button onClick={() => void run(() => api(`/admin/indicators/${i.code}`, { method: 'DELETE' }), `${i.code}: видалено.`)}>
                        Видалити
                      </button>
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </>
      )}

      {tab === 'categories' && (
        <>
          <form
            className="filters"
            onSubmit={(e) => {
              e.preventDefault();
              const f = e.currentTarget;
              void run(() => post('/admin/categories', formObject(e)), 'Категорію додано.', f);
            }}
          >
            <label>
              Код
              <input name="code" required pattern="[A-Z][A-Z0-9_]{1,19}" placeholder="ENERGY" />
            </label>
            <label>
              Назва українською
              <input name="name_ua" required />
            </label>
            <label>
              Назва англійською
              <input name="name_en" required />
            </label>
            <div>
              <button type="submit">Додати категорію</button>
            </div>
          </form>
          <table>
            <thead>
              <tr>
                <th>Код</th>
                <th>Назва українською</th>
                <th>Назва англійською</th>
              </tr>
            </thead>
            <tbody>
              {categories.data?.map((c) => (
                <tr key={c.code}>
                  <td>
                    <code>{c.code}</code>
                  </td>
                  <td>{c.name_ua}</td>
                  <td>{c.name_en}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
