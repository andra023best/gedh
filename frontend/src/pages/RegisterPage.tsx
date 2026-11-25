// UC-01 «Зареєструвати обліковий запис» (FR-01, FR-02, FR-04; DG-09, DG-10).
import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../lib/messages';
import { api, ApiError } from '../lib/session';

type Country = { code: string; name_ua: string; is_active?: boolean };
type Org = { id: string; name: string; country_code: string };
type Consent = { text_version: string; text: string };

const OTHER = '__other__';

export default function RegisterPage() {
  const countries = useQuery({
    queryKey: ['countries'],
    // українська абетка (Є, І, Ї на своїх місцях) — порядок у БД залежить від її налаштувань
    // лише активні країни: деактивовану адміністратором країну обрати не можна (S-41)
    queryFn: async () =>
      (await api<Country[]>('/countries'))
        .filter((c) => c.is_active !== false)
        .sort((a, b) => a.name_ua.localeCompare(b.name_ua, 'uk')),
  });
  const orgs = useQuery({ queryKey: ['organizations'], queryFn: () => api<Org[]>('/organizations') });
  const consent = useQuery({ queryKey: ['consent'], queryFn: () => api<Consent>('/auth/consent') });

  const [accountType, setAccountType] = useState<'INDIVIDUAL' | 'ORGANIZATION_MEMBER'>('INDIVIDUAL');
  const [orgChoice, setOrgChoice] = useState('');
  const [consentAccepted, setConsentAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const form = new FormData(e.currentTarget);
    if (form.get('password') !== form.get('password2')) {
      setError('Паролі не збігаються.');
      return;
    }
    form.delete('password2');
    form.set('account_type', accountType);
    form.set('consent_accepted', String(consentAccepted));
    form.set('consent_text_version', consent.data?.text_version ?? '');
    if (accountType === 'ORGANIZATION_MEMBER' && orgChoice && orgChoice !== OTHER) form.set('organization_id', orgChoice);
    if (accountType !== 'ORGANIZATION_MEMBER' || orgChoice !== OTHER) form.delete('organization_name');
    const doc = form.get('verification_document');
    if (doc instanceof File && doc.size === 0) form.delete('verification_document');
    if (accountType !== 'ORGANIZATION_MEMBER') form.delete('verification_document');
    setBusy(true);
    try {
      const res = await api<{ message: string }>('/auth/register', { method: 'POST', body: form });
      setDone(res.message);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && Array.isArray(err.body.detail)) {
        setError('Перевірте заповнення полів: ПІБ (≥ 2 символи), мета використання (≥ 10 символів).');
      } else {
        setError(errorText(err));
      }
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <section className="narrow">
        <h1>Перевірте пошту</h1>
        <p>{done}</p>
        <p>
          Посилання в листі дійсне 24 год. Якщо листа немає, перевірте «Спам». Після підтвердження e-mail заявку розгляне
          адміністратор.
        </p>
      </section>
    );
  }

  return (
    <section className="narrow">
      <h1>Реєстрація</h1>
      <form onSubmit={submit} className="form">
        <label>
          ПІБ
          <input name="full_name" required minLength={2} maxLength={200} />
        </label>
        <label>
          E-mail
          <input name="email" type="email" required autoComplete="email" />
        </label>
        <label>
          Пароль (≥ 10 символів, літери й цифри)
          <input name="password" type="password" required minLength={10} autoComplete="new-password" />
        </label>
        <label>
          Повторіть пароль
          <input name="password2" type="password" required autoComplete="new-password" />
        </label>
        <label>
          Країна
          <select name="country_code" required defaultValue="">
            <option value="" disabled>
              — оберіть —
            </option>
            {countries.data?.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name_ua}
              </option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend>Тип облікового запису</legend>
          <label className="inline">
            <input
              type="radio"
              checked={accountType === 'INDIVIDUAL'}
              onChange={() => setAccountType('INDIVIDUAL')}
            />{' '}
            Індивідуальний аналітик (відкриті дані)
          </label>
          <label className="inline">
            <input
              type="radio"
              checked={accountType === 'ORGANIZATION_MEMBER'}
              onChange={() => setAccountType('ORGANIZATION_MEMBER')}
            />{' '}
            Представник організації (заявки на закриті дані, завантаження наборів)
          </label>
        </fieldset>
        {accountType === 'ORGANIZATION_MEMBER' && (
          <>
            <label>
              Організація
              <select value={orgChoice} onChange={(e) => setOrgChoice(e.target.value)} required>
                <option value="" disabled>
                  — оберіть —
                </option>
                {orgs.data?.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name} ({o.country_code})
                  </option>
                ))}
                <option value={OTHER}>Моєї організації немає в списку</option>
              </select>
            </label>
            {orgChoice === OTHER && (
              <label>
                Назва організації
                <input name="organization_name" required maxLength={200} />
              </label>
            )}
            <label>
              Документ, що підтверджує працевлаштування (PDF, PNG або JPEG до 2 МБ). Не потрібен, якщо e-mail на домені
              організації.
              <input name="verification_document" type="file" accept=".pdf,.png,.jpg,.jpeg" />
            </label>
          </>
        )}
        <label>
          Посада (необов'язково)
          <input name="position" maxLength={200} />
        </label>
        <label>
          Мета використання платформи
          <textarea name="purpose" required minLength={10} maxLength={2000} rows={3} />
        </label>
        <div className="consent">
          <p>
            <strong>Згода на обробку персональних даних</strong> (версія {consent.data?.text_version ?? '…'})
          </p>
          <pre className="consent-text">{consent.data?.text ?? 'Завантаження…'}</pre>
          <label className="inline">
            <input type="checkbox" checked={consentAccepted} onChange={(e) => setConsentAccepted(e.target.checked)} />{' '}
            Я ознайомився(-лася) з текстом і надаю згоду
          </label>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || !consent.data}>
          {busy ? 'Надсилання…' : 'Зареєструватися'}
        </button>
      </form>
      <p>
        Уже маєте обліковий запис? <Link to="/login">Увійти</Link>
      </p>
    </section>
  );
}
