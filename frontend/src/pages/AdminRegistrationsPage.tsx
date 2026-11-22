// UC-02 «Розглянути заявку на реєстрацію» (FR-03, FR-04; D-01).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { blobFetch } from '../lib/download';
import { errorText, ROLE_UK, STATUS_UK } from '../lib/messages';
import { api, post } from '../lib/session';

type Org = { id: string; name: string; type: string; country_code: string; status?: string };
type Registration = {
  id: string;
  status: string;
  account_type: string;
  purpose: string;
  submitted_at: string;
  decision_reason: string | null;
  has_document: boolean;
  email_domain_matches_organization: boolean | null;
  organization_name: string | null;
  clarification_note: string | null;
  clarification_answer: string | null;
  applicant: {
    full_name: string;
    email: string;
    position: string | null;
    country_code: string;
    organization: Org | null;
  };
};

// Документ потребує токена, тому завантажуємо через fetch і віддаємо браузеру як файл
const downloadDocument = (id: string) => blobFetch(`/admin/registrations/${id}/document`);

export default function AdminRegistrationsPage() {
  const qc = useQueryClient();
  const queue = useQuery({
    queryKey: ['registrations'],
    queryFn: () => api<{ items: Registration[]; total: number }>('/admin/registrations'),
  });
  const orgs = useQuery({ queryKey: ['admin-orgs'], queryFn: () => api<Org[]>('/admin/organizations') });
  const [selected, setSelected] = useState<string | null>(null);
  const current = queue.data?.items.find((r) => r.id === selected) ?? null;

  return (
    <section>
      <h1>Заявки на реєстрацію</h1>
      {queue.isLoading && <p>Завантаження…</p>}
      {queue.error && <p className="error">{errorText(queue.error)}</p>}
      {queue.data && queue.data.items.length === 0 && <p>Черга порожня.</p>}
      {queue.data && queue.data.items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Подано</th>
              <th>Заявник</th>
              <th>Тип</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {queue.data.items.map((r) => (
              <tr key={r.id} className={r.id === selected ? 'selected' : undefined}>
                <td>{new Date(r.submitted_at).toLocaleString('uk-UA')}</td>
                <td>
                  {r.applicant.full_name}
                  <br />
                  <small>{r.applicant.email}</small>
                </td>
                <td>{r.account_type === 'INDIVIDUAL' ? 'індивідуальний' : 'представник організації'}</td>
                <td>{STATUS_UK[r.status] ?? r.status}</td>
                <td>
                  <button onClick={() => setSelected(r.id)}>Розглянути</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {current && (
        <Review
          key={current.id}
          r={current}
          orgs={orgs.data ?? []}
          onDone={() => {
            setSelected(null);
            void qc.invalidateQueries({ queryKey: ['registrations'] });
            void qc.invalidateQueries({ queryKey: ['admin-orgs'] });
          }}
        />
      )}
    </section>
  );
}

function Review({ r, orgs, onDone }: { r: Registration; orgs: Org[]; onDone: () => void }) {
  const verified = orgs.filter((o) => o.status === 'VERIFIED');
  const [role, setRole] = useState(r.account_type === 'INDIVIDUAL' ? 'ANALYST' : 'ORG_REPRESENTATIVE');
  const [orgId, setOrgId] = useState(r.applicant.organization?.id ?? '');
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const needsOrg = role !== 'ANALYST';

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => post(`/admin/registrations/${r.id}/${path}`, body),
    onSuccess: onDone,
    onError: (err) => setError(errorText(err)),
  });

  const createOrg = useMutation({
    mutationFn: () =>
      post<Org>('/admin/organizations', {
        name: r.organization_name,
        type: 'ANALYTICS_COMPANY',
        country_code: r.applicant.country_code,
      }),
    onSuccess: (org) => {
      setOrgId(org.id);
      onDone();
    },
    onError: (err) => setError(errorText(err)),
  });

  return (
    <div className="card">
      <h2>{r.applicant.full_name}</h2>
      <dl className="kv">
        <dt>E-mail</dt>
        <dd>
          {r.applicant.email}
          {r.email_domain_matches_organization === true && ' ✅ домен збігається з доменом організації'}
          {r.email_domain_matches_organization === false && ' ⚠️ домен не збігається з доменом організації'}
        </dd>
        <dt>Країна</dt>
        <dd>{r.applicant.country_code}</dd>
        <dt>Посада</dt>
        <dd>{r.applicant.position || '—'}</dd>
        <dt>Організація</dt>
        <dd>{r.applicant.organization?.name ?? r.organization_name ?? '—'}</dd>
        <dt>Мета</dt>
        <dd>{r.purpose}</dd>
        <dt>Документ</dt>
        <dd>
          {r.has_document ? (
            <button type="button" onClick={() => void downloadDocument(r.id)}>
              Завантажити документ
            </button>
          ) : (
            'немає'
          )}
        </dd>
        {r.clarification_note && (
          <>
            <dt>Уточнення</dt>
            <dd>
              Питання: {r.clarification_note}
              <br />
              Відповідь: {r.clarification_answer ?? 'ще немає'}
            </dd>
          </>
        )}
      </dl>

      <h3>Схвалити</h3>
      <label>
        Роль{' '}
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {['ANALYST', 'ORG_REPRESENTATIVE', 'DATA_PROVIDER'].map((x) => (
            <option key={x} value={x}>
              {ROLE_UK[x]}
            </option>
          ))}
        </select>
      </label>
      {needsOrg && (
        <label>
          {' '}
          Верифікована організація{' '}
          <select value={orgId} onChange={(e) => setOrgId(e.target.value)}>
            <option value="">— оберіть —</option>
            {verified.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name} ({o.country_code})
              </option>
            ))}
          </select>
        </label>
      )}
      {needsOrg && r.organization_name && !r.applicant.organization && (
        <p>
          Організації «{r.organization_name}» немає в довіднику.{' '}
          <button type="button" onClick={() => createOrg.mutate()} disabled={createOrg.isPending}>
            Створити й верифікувати її
          </button>
        </p>
      )}
      <p>
        <button
          disabled={act.isPending || (needsOrg && !orgId)}
          onClick={() => act.mutate({ path: 'approve', body: { role, organization_id: needsOrg ? orgId : null } })}
        >
          Схвалити
        </button>
      </p>

      <h3>Відхилити або запитати уточнення</h3>
      <textarea
        rows={3}
        placeholder="Причина відхилення або питання до заявника (щонайменше 5 символів)"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <p>
        <button
          className="danger"
          disabled={act.isPending || text.trim().length < 5}
          onClick={() => act.mutate({ path: 'reject', body: { reason: text.trim() } })}
        >
          Відхилити
        </button>{' '}
        <button
          disabled={act.isPending || text.trim().length < 5}
          onClick={() => act.mutate({ path: 'clarify', body: { note: text.trim() } })}
        >
          Запитати уточнення
        </button>
      </p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
