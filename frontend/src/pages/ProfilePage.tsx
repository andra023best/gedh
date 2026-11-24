// UC-21 «Керувати персональними даними» (FR-34, FR-42; DG-12, DG-24).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { blobFetch } from '../lib/download';
import { errorText, ROLE_UK } from '../lib/messages';
import { api, clearSession, post } from '../lib/session';

type Me = {
  id: string;
  full_name: string;
  email: string;
  position: string | null;
  country_code: string;
  role: string;
  created_at: string;
  organization: { name: string } | null;
  consent: { accepted_version: string | null; current_version: string; accepted_at: string | null };
  retention: { data: string; purpose: string; retention: string }[];
};

export default function ProfilePage() {
  const [exported, setExported] = useState<string | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const me = useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') });
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: { full_name: string; position: string | null }) =>
      api<Me>('/me', { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: (data) => {
      qc.setQueryData(['me'], data);
      setEditing(false);
      setMsg('Зміни збережено.');
    },
    onError: (err) => setMsg(errorText(err)),
  });

  const [del, setDel] = useState({ open: false, password: '', confirm: false, reason: 'DELETE_ACCOUNT' });
  const [delError, setDelError] = useState<string | null>(null);

  async function deleteAccount(e: FormEvent) {
    e.preventDefault();
    setDelError(null);
    try {
      await post('/me/delete', { password: del.password, reason: del.reason, confirm_data_deletion: del.confirm });
      // спершу йдемо із захищеної сторінки, інакше вона сама перекине на вхід
      navigate('/', { replace: true, state: { notice: 'Обліковий запис деактивовано. Персональні дані буде анонімізовано через 30 днів.' } });
      clearSession();
      qc.clear();
    } catch (err) {
      setDelError(errorText(err));
    }
  }

  if (me.isLoading) return <p>Завантаження…</p>;
  if (me.error || !me.data) return <p className="error">{errorText(me.error)}</p>;
  const m = me.data;

  return (
    <section>
      <h1>Мій профіль</h1>
      {msg && <p className="ok">{msg}</p>}
      {!editing ? (
        <dl className="kv">
          <dt>ПІБ</dt>
          <dd>{m.full_name}</dd>
          <dt>E-mail</dt>
          <dd>{m.email}</dd>
          <dt>Посада</dt>
          <dd>{m.position || '—'}</dd>
          <dt>Країна</dt>
          <dd>{m.country_code}</dd>
          <dt>Роль</dt>
          <dd>{ROLE_UK[m.role] ?? m.role}</dd>
          <dt>Організація</dt>
          <dd>{m.organization?.name ?? '—'}</dd>
          <dt>Згода</dt>
          <dd>
            версія {m.consent.accepted_version ?? '—'}
            {m.consent.accepted_at && `, прийнято ${new Date(m.consent.accepted_at).toLocaleString('uk-UA')}`}
          </dd>
        </dl>
      ) : (
        <form
          className="form"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            save.mutate({ full_name: String(f.get('full_name')), position: String(f.get('position')) || null });
          }}
        >
          <label>
            ПІБ
            <input name="full_name" defaultValue={m.full_name} required minLength={2} maxLength={200} />
          </label>
          <label>
            Посада
            <input name="position" defaultValue={m.position ?? ''} maxLength={200} />
          </label>
          <p className="hint">Зміна організації потребує повторної верифікації — ця функція з'явиться пізніше.</p>
          <button type="submit" disabled={save.isPending}>
            Зберегти
          </button>{' '}
          <button type="button" onClick={() => setEditing(false)}>
            Скасувати
          </button>
        </form>
      )}
      {!editing && <button onClick={() => setEditing(true)}>Виправити дані</button>}

      <h2>Що про мене зберігається</h2>
      <table>
        <thead>
          <tr>
            <th>Дані</th>
            <th>Навіщо</th>
            <th>Скільки зберігаються</th>
          </tr>
        </thead>
        <tbody>
          {m.retention.map((r) => (
            <tr key={r.data}>
              <td>{r.data}</td>
              <td>{r.purpose}</td>
              <td>{r.retention}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Мої дані у файлі</h2>
      <p>
        Копія всіх ваших персональних даних, заявок, дозволів та історії входів у форматі JSON (UC-21, право
        на доступ і перенесення даних). Файл формується одразу й на сервері не зберігається.
      </p>
      <p>
        <button onClick={() => void blobFetch('/me/export').then((h) => setExported(h.get('x-content-sha256')))}>
          Завантажити мої дані (JSON)
        </button>
        {exported && <span className="hint"> Файл збережено. SHA-256: {exported}</span>}
      </p>

      <h2>Видалити обліковий запис або відкликати згоду</h2>
      {!del.open ? (
        <button className="danger" onClick={() => setDel({ ...del, open: true })}>
          Видалити обліковий запис…
        </button>
      ) : (
        <form className="form danger-box" onSubmit={deleteAccount}>
          <p>
            Ви втратите доступ до платформи, а всі ваші чинні дозволи на закриті дані буде завершено. Персональні дані
            анонімізуються через 30 днів. Відкликання згоди також означає видалення облікового запису.
          </p>
          <label className="inline">
            <input
              type="radio"
              checked={del.reason === 'DELETE_ACCOUNT'}
              onChange={() => setDel({ ...del, reason: 'DELETE_ACCOUNT' })}
            />{' '}
            Видалити обліковий запис
          </label>
          <label className="inline">
            <input
              type="radio"
              checked={del.reason === 'WITHDRAW_CONSENT'}
              onChange={() => setDel({ ...del, reason: 'WITHDRAW_CONSENT' })}
            />{' '}
            Відкликати згоду на обробку персональних даних
          </label>
          <label className="inline">
            <input type="checkbox" checked={del.confirm} onChange={(e) => setDel({ ...del, confirm: e.target.checked })} />{' '}
            Підтверджую, що видалив(-ла) дані, отримані за всіма дозволами (DG-12)
          </label>
          <label>
            Пароль для підтвердження
            <input
              type="password"
              required
              autoComplete="current-password"
              value={del.password}
              onChange={(e) => setDel({ ...del, password: e.target.value })}
            />
          </label>
          {delError && <p className="error">{delError}</p>}
          <button type="submit" className="danger" disabled={!del.confirm}>
            Підтвердити видалення
          </button>{' '}
          <button type="button" onClick={() => setDel({ ...del, open: false })}>
            Скасувати
          </button>
        </form>
      )}
    </section>
  );
}
