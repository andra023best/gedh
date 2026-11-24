// Обмін закритими даними (UC-08 – UC-11, UC-16, UC-19): заявки з двома погодженнями, угода, дозволи, вивантаження.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../lib/authContext';
import { GRANT_STATUS_UK, REQUEST_STATUS_UK } from '../lib/datasetStatus';
import { blobFetch } from '../lib/download';
import { errorText } from '../lib/messages';
import { api, ApiError, post } from '../lib/session';

type Period = { start: string | null; end: string | null };
type Decision = {
  stage: 'ADMIN' | 'PROVIDER';
  decision: string;
  reason: string | null;
  approved_period: Period | null;
  approved_term_days: number | null;
  decided_at: string;
  decided_by: string | null;
};
type Req = {
  id: string;
  number: string;
  status: string;
  submitted_at: string;
  country_code: string;
  category_code: string;
  period: Period;
  purpose: string;
  usage_place: string;
  requested_term_days: number;
  responsible_person: string;
  requester: string | null;
  organization: { name: string } | null;
  target_dataset_title: string | null;
  decisions: Decision[];
  clarification_note: string | null;
  clarification_answer: string | null;
  agreement_id: string | null;
  offer_expires_at: string | null;
  grant_id: string | null;
};
type Agreement = { text: string; text_hash: string; offer_expires_at: string; accepted_at: string | null };
type Grant = {
  id: string;
  request_number: string | null;
  dataset_id: string;
  dataset_title: string | null;
  country_code: string | null;
  category_code: string | null;
  period: Period;
  valid_from: string;
  valid_to: string;
  status: string;
  deletion_confirmed_at: string | null;
  grantee: string | null;
  is_synthetic: boolean | null;
};
type Named = { code: string; name_ua: string };

const d = (s: string | null) => (s ? new Date(s).toLocaleDateString('uk-UA') : '—');
const STEPS: [string, string[]][] = [
  ['Подано', ['SUBMITTED', 'NEEDS_CLARIFICATION']],
  ['Адміністратор', ['PROVIDER_REVIEW']],
  ['Постачальник', ['AWAITING_DATA', 'AWAITING_AGREEMENT']],
  ['Угода', ['GRANTED']],
  ['Дозвіл', ['GRANTED', 'EXPIRED']],
];

function Timeline({ status }: { status: string }) {
  // шкала статусів (S-23): видно, на якому кроці заявка й хто зараз має діяти
  const reached = STEPS.findIndex(([, st]) => st.includes(status));
  const failed = ['REJECTED', 'CANCELLED'].includes(status);
  return (
    <p className="timeline">
      {STEPS.map(([name], i) => (
        <span key={name} className={!failed && i <= reached ? 'step done' : 'step'}>
          {name}
        </span>
      ))}
      <strong> — {REQUEST_STATUS_UK[status] ?? status}</strong>
    </p>
  );
}

function NewRequestForm({ onCreated }: { onCreated: (r: Req) => void }) {
  const [params] = useSearchParams();
  const countries = useQuery({ queryKey: ['countries'], queryFn: () => api<Named[]>('/countries') });
  const categories = useQuery({ queryKey: ['categories'], queryFn: () => api<Named[]>('/categories') });
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const f = new FormData(e.currentTarget);
    try {
      const r = await post<Req>('/access-requests', {
        country_code: f.get('country_code'),
        category_code: f.get('category_code'),
        period: { start: f.get('start'), end: f.get('end') },
        purpose: f.get('purpose'),
        usage_place: f.get('usage_place'),
        requested_term_days: Number(f.get('term')),
        responsible_person: f.get('responsible_person'),
      });
      e.currentTarget?.reset();
      onCreated(r);
    } catch (err) {
      const codes: Record<string, string> = {
        NO_PROVIDER_FOR_COUNTRY: 'Для цієї країни на платформі немає постачальника — закриті дані недоступні.',
        DUPLICATE_ACTIVE_REQUEST: 'Ваша організація вже має активну заявку на цю країну й категорію.',
        DELETION_CONFIRMATION_REQUIRED:
          'Спершу підтвердьте видалення даних за завершеним дозволом (сторінка «Дозволи», DG-12).',
        ORGANIZATION_NOT_VERIFIED: 'Подавати заявки може лише представник верифікованої організації.',
        INVALID_PERIOD: 'Початок періоду має бути не пізніше за кінець.',
      };
      setError(err instanceof ApiError && codes[err.code] ? codes[err.code] : errorText(err));
    }
  }

  return (
    <form className="form card" onSubmit={submit}>
      <h2>Нова заявка на закриті дані</h2>
      <label>
        Країна
        <select name="country_code" required defaultValue={params.get('country') ?? ''}>
          <option value="" disabled>
            — оберіть —
          </option>
          {countries.data
            ?.slice()
            .sort((a, b) => a.name_ua.localeCompare(b.name_ua, 'uk'))
            .map((c) => (
              <option key={c.code} value={c.code}>
                {c.name_ua}
              </option>
            ))}
        </select>
      </label>
      <label>
        Категорія даних
        <select name="category_code" required defaultValue={params.get('category') ?? ''}>
          <option value="" disabled>
            — оберіть —
          </option>
          {categories.data?.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name_ua}
              </option>
            ))}
        </select>
      </label>
      <label>
        Період даних: з
        <input name="start" type="date" required />
      </label>
      <label>
        по
        <input name="end" type="date" required />
      </label>
      <label>
        Мета використання
        <textarea name="purpose" required minLength={10} maxLength={2000} rows={2} />
      </label>
      <label>
        Місце й спосіб використання
        <input name="usage_place" required minLength={2} maxLength={300} />
      </label>
      <label>
        Бажаний строк доступу, днів (до 180)
        <input name="term" type="number" min={1} max={180} defaultValue={90} required />
      </label>
      <label>
        Відповідальна особа
        <input name="responsible_person" required minLength={2} maxLength={200} />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit">Подати заявку</button>
    </form>
  );
}

function RequestDetail({ req, onChanged }: { req: Req; onChanged: (r: Req) => void }) {
  const { user } = useAuth();
  const role = user?.role;
  const [text, setText] = useState('');
  const [narrow, setNarrow] = useState({ start: req.period.start ?? '', end: req.period.end ?? '', term: req.requested_term_days });
  const [error, setError] = useState<string | null>(null);
  const agreement = useQuery({
    queryKey: ['agreement', req.id],
    queryFn: () => api<Agreement>(`/access-requests/${req.id}/agreement`),
    enabled: Boolean(req.agreement_id),
  });
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post<Req>(`/access-requests/${req.id}/${path}`, body),
    onSuccess: (r) => {
      setError(null);
      setText('');
      onChanged(r);
    },
    onError: (err) => setError(errorText(err)),
  });
  const accept = useMutation({
    mutationFn: () => post<Grant>(`/access-requests/${req.id}/agreement/accept`, { text_hash: agreement.data?.text_hash }),
    onSuccess: async () => onChanged(await api<Req>(`/access-requests/${req.id}`)),
    onError: (err) => setError(errorText(err)),
  });
  const needReason = text.trim().length < 5;

  return (
    <div className="card">
      <h2>Заявка {req.number}</h2>
      <Timeline status={req.status} />
      <dl className="kv">
        <dt>Організація</dt>
        <dd>
          {req.organization?.name} · заявник {req.requester}
        </dd>
        <dt>Обсяг</dt>
        <dd>
          {req.country_code} × {req.category_code}, період {d(req.period.start)} — {d(req.period.end)}, строк{' '}
          {req.requested_term_days} дн.
        </dd>
        <dt>Мета</dt>
        <dd>{req.purpose}</dd>
        <dt>Місце використання</dt>
        <dd>{req.usage_place}</dd>
        <dt>Відповідальна особа</dt>
        <dd>{req.responsible_person}</dd>
        <dt>Набір</dt>
        <dd>{req.target_dataset_title ?? 'ще немає на платформі'}</dd>
        {req.clarification_note && (
          <>
            <dt>Уточнення</dt>
            <dd>
              Питання: {req.clarification_note}
              <br />
              Відповідь: {req.clarification_answer ?? 'ще немає'}
            </dd>
          </>
        )}
      </dl>
      {req.decisions.length > 0 && (
        <>
          <h3>Рішення</h3>
          <ul>
            {req.decisions.map((x) => (
              <li key={x.stage}>
                {x.stage === 'ADMIN' ? 'Адміністратор' : 'Постачальник'}: {x.decision}
                {x.approved_period && ` · період ${d(x.approved_period.start)} — ${d(x.approved_period.end)}`}
                {x.approved_term_days && ` · строк ${x.approved_term_days} дн.`}
                {x.reason && ` · ${x.reason}`} ({x.decided_by}, {d(x.decided_at)})
              </li>
            ))}
          </ul>
        </>
      )}

      {role === 'ADMIN' && req.status === 'SUBMITTED' && (
        <div>
          <h3>Перше погодження: хто й навіщо</h3>
          <button onClick={() => act.mutate({ path: 'admin-decision', body: { decision: 'APPROVED' } })}>Погодити</button>
          <p>
            <textarea rows={2} placeholder="Причина відхилення або питання до заявника (≥ 5 символів)" value={text} onChange={(e) => setText(e.target.value)} />
          </p>
          <button className="danger" disabled={needReason} onClick={() => act.mutate({ path: 'admin-decision', body: { decision: 'REJECTED', reason: text } })}>
            Відхилити
          </button>{' '}
          <button disabled={needReason} onClick={() => act.mutate({ path: 'admin-decision', body: { decision: 'NEEDS_CLARIFICATION', reason: text } })}>
            Запитати уточнення
          </button>
        </div>
      )}

      {role === 'DATA_PROVIDER' && req.status === 'PROVIDER_REVIEW' && (
        <div>
          <h3>Друге погодження: що й скільки</h3>
          <button onClick={() => act.mutate({ path: 'provider-decision', body: { decision: 'APPROVED' } })}>Погодити повністю</button>
          <p>Або звузити обсяг (у межах запитаного):</p>
          <label>
            з <input type="date" value={narrow.start} onChange={(e) => setNarrow({ ...narrow, start: e.target.value })} />
          </label>{' '}
          <label>
            по <input type="date" value={narrow.end} onChange={(e) => setNarrow({ ...narrow, end: e.target.value })} />
          </label>{' '}
          <label>
            строк, днів <input type="number" min={1} max={req.requested_term_days} value={narrow.term} onChange={(e) => setNarrow({ ...narrow, term: Number(e.target.value) })} />
          </label>{' '}
          <button
            onClick={() =>
              act.mutate({
                path: 'provider-decision',
                body: { decision: 'APPROVED_NARROWED', approved_period: { start: narrow.start, end: narrow.end }, approved_term_days: narrow.term },
              })
            }
          >
            Погодити звужений обсяг
          </button>
          <p>
            <textarea rows={2} placeholder="Причина відхилення (≥ 5 символів)" value={text} onChange={(e) => setText(e.target.value)} />
          </p>
          <button className="danger" disabled={needReason} onClick={() => act.mutate({ path: 'provider-decision', body: { decision: 'REJECTED', reason: text } })}>
            Відхилити
          </button>
        </div>
      )}

      {role === 'ORG_REPRESENTATIVE' && req.status === 'NEEDS_CLARIFICATION' && (
        <div>
          <h3>Відповідь на уточнення</h3>
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
          <p>
            <button disabled={needReason} onClick={() => act.mutate({ path: 'clarification', body: { answer: text } })}>
              Надіслати
            </button>
          </p>
        </div>
      )}

      {req.agreement_id && agreement.data && (
        <div>
          <h3>Угода про обмін даними</h3>
          <pre className="consent-text">{agreement.data.text}</pre>
          <p className="hint">
            SHA-256 тексту: <code>{agreement.data.text_hash.slice(0, 16)}…</code>
            {!agreement.data.accepted_at && ` · пропозиція чинна до ${d(agreement.data.offer_expires_at)}`}
            {agreement.data.accepted_at && ` · прийнято ${d(agreement.data.accepted_at)}`}
          </p>
          {role === 'ORG_REPRESENTATIVE' && req.status === 'AWAITING_AGREEMENT' && (
            <>
              <button onClick={() => accept.mutate()}>Приймаю умови угоди</button>{' '}
              <button className="danger" onClick={() => act.mutate({ path: 'agreement/decline' })}>
                Відмовитися
              </button>
            </>
          )}
        </div>
      )}
      {role === 'ORG_REPRESENTATIVE' && ['SUBMITTED', 'NEEDS_CLARIFICATION', 'PROVIDER_REVIEW', 'AWAITING_DATA'].includes(req.status) && (
        <p>
          <button className="danger" onClick={() => act.mutate({ path: 'cancel' })}>
            Скасувати заявку
          </button>
        </p>
      )}
      {req.grant_id && <p className="ok">Дозвіл видано — дані на сторінці «Дозволи».</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export function RequestsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const list = useQuery({ queryKey: ['requests'], queryFn: () => api<{ items: Req[] }>('/access-requests') });
  const [selected, setSelected] = useState<Req | null>(null);
  const refresh = (r: Req) => {
    setSelected(r);
    void qc.invalidateQueries({ queryKey: ['requests'] });
  };
  const title = { ADMIN: 'Заявки на закриті дані — перше погодження', DATA_PROVIDER: 'Заявки на дані вашої країни' }[
    user?.role as string
  ];

  return (
    <section>
      <h1>{title ?? 'Заявки вашої організації'}</h1>
      {user?.role === 'ORG_REPRESENTATIVE' && <NewRequestForm onCreated={refresh} />}
      {list.isLoading && <p>Завантаження…</p>}
      {list.error && <p className="error">{errorText(list.error)}</p>}
      {list.data?.items.length === 0 && <p>Заявок поки немає.</p>}
      {list.data && list.data.items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Номер</th>
              <th>Подано</th>
              <th>Організація</th>
              <th>Обсяг</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {list.data.items.map((r) => (
              <tr key={r.id} className={r.id === selected?.id ? 'selected' : undefined}>
                <td>{r.number}</td>
                <td>{d(r.submitted_at)}</td>
                <td>{r.organization?.name}</td>
                <td>
                  {r.country_code} × {r.category_code}
                </td>
                <td>{REQUEST_STATUS_UK[r.status] ?? r.status}</td>
                <td>
                  <button onClick={() => setSelected(r)}>Відкрити</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {selected && <RequestDetail key={selected.id + selected.status} req={selected} onChanged={refresh} />}
    </section>
  );
}

type Obs = {
  items: { breakdown: string | null; indicator: string; period: { start: string }; value: number | null; note: string | null }[];
  coverage: string;
  covered_period: Period | null;
  synthetic_notice: string | null;
  version_no: number;
};

function GrantData({ grant }: { grant: Grant }) {
  const obs = useQuery({
    queryKey: ['grant-data', grant.id],
    queryFn: () => api<Obs>(`/datasets/${grant.dataset_id}/observations`),
  });
  const [msg, setMsg] = useState<string | null>(null);
  async function download(format: 'csv' | 'json') {
    try {
      const h = await blobFetch(`/datasets/${grant.dataset_id}/export?format=${format}`);
      setMsg(`Файл завантажено. SHA-256: ${h.get('x-content-sha256')?.slice(0, 16)}… — записано в журнал аудиту.`);
    } catch (err) {
      setMsg(errorText(err));
    }
  }
  if (obs.isLoading) return <p>Завантаження…</p>;
  if (obs.error) return <p className="error">{errorText(obs.error)}</p>;
  const o = obs.data!;
  const periods = [...new Set(o.items.map((i) => i.period.start))].sort();
  const rows = [...new Set(o.items.map((i) => `${i.indicator} · ${i.breakdown ?? '—'}`))];
  const cell = new Map(o.items.map((i) => [`${i.indicator} · ${i.breakdown ?? '—'}|${i.period.start}`, i]));
  return (
    <div className="card">
      {o.synthetic_notice && <p className="synthetic">⚠ {o.synthetic_notice}</p>}
      <p className="hint">
        Версія {o.version_no} · видано в межах дозволу {d(o.covered_period?.start ?? null)} — {d(o.covered_period?.end ?? null)}
        {o.coverage === 'PARTIAL' && ' (частина набору поза дозволом не показується)'}
      </p>
      <div className="scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Показник · розріз</th>
              {periods.map((p) => (
                <th key={p}>{p.slice(0, 7)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r}>
                <td>{r}</td>
                {periods.map((p) => {
                  const v = cell.get(`${r}|${p}`);
                  return (
                    <td key={p} className="num" title={v?.note ?? undefined}>
                      {v?.value ?? <span className="na">н/д</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <button onClick={() => void download('csv')}>Вивантажити CSV</button>{' '}
        <button onClick={() => void download('json')}>Вивантажити JSON</button>
      </p>
      {msg && <p className="hint">{msg}</p>}
    </div>
  );
}

export function GrantsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const grants = useQuery({ queryKey: ['grants'], queryFn: () => api<Grant[]>('/grants') });
  const [open, setOpen] = useState<string | null>(null);
  const confirm = useMutation({
    mutationFn: (id: string) => post<Grant>(`/grants/${id}/confirm-deletion`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['grants'] }),
  });
  const mine = user?.role === 'ORG_REPRESENTATIVE';

  return (
    <section>
      <h1>{mine ? 'Мої дозволи' : 'Дозволи на закриті дані'}</h1>
      {grants.data?.length === 0 && <p>Дозволів поки немає.</p>}
      {grants.data && grants.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Заявка</th>
              <th>Набір</th>
              <th>Період даних</th>
              <th>Діє</th>
              <th>Статус</th>
              {!mine && <th>Отримувач</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {grants.data.map((g) => (
              <tr key={g.id}>
                <td>{g.request_number}</td>
                <td>
                  {g.dataset_title}
                  {g.is_synthetic && <div className="synthetic">СИНТЕТИЧНІ ДАНІ</div>}
                </td>
                <td>
                  {d(g.period.start)} — {d(g.period.end)}
                </td>
                <td>
                  {d(g.valid_from)} — {d(g.valid_to)}
                </td>
                <td>{GRANT_STATUS_UK[g.status] ?? g.status}</td>
                {!mine && <td>{g.grantee}</td>}
                <td>
                  {mine && g.status === 'ACTIVE' && <button onClick={() => setOpen(open === g.id ? null : g.id)}>Дані</button>}
                  {mine && g.status === 'EXPIRED' && !g.deletion_confirmed_at && (
                    <button onClick={() => confirm.mutate(g.id)}>Підтвердити видалення даних</button>
                  )}
                  {g.deletion_confirmed_at && <span className="hint">видалення підтверджено {d(g.deletion_confirmed_at)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {mine && grants.data?.some((g) => g.status === 'EXPIRED' && !g.deletion_confirmed_at) && (
        <p className="warn">
          За угодою після завершення строку дозволу дані треба видалити й підтвердити це тут. До підтвердження нові заявки
          організації не приймаються (DG-12).
        </p>
      )}
      {open && grants.data && <GrantData grant={grants.data.find((g) => g.id === open)!} />}
    </section>
  );
}
