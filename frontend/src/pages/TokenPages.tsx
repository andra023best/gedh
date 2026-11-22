// Сторінки за одноразовими посиланнями з листів: підтвердження e-mail (UC-01) і уточнення заявки (UC-02 AC1).
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { errorText } from '../lib/messages';
import { api, ApiError, post } from '../lib/session';

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const [state, setState] = useState<{ ok: boolean; text: string; expired?: boolean } | null>(null);
  const started = useRef(false);
  const [email, setEmail] = useState('');
  const [resent, setResent] = useState(false);

  useEffect(() => {
    if (started.current) return; // StrictMode викликає ефект двічі, а токен одноразовий
    started.current = true;
    post<{ message: string }>('/auth/verify-email', { token })
      .then((r) => setState({ ok: true, text: r.message }))
      .catch((err) =>
        setState({ ok: false, text: errorText(err), expired: err instanceof ApiError && err.code === 'TOKEN_EXPIRED' }),
      );
  }, [token]);

  async function resend(e: FormEvent) {
    e.preventDefault();
    await post('/auth/resend-verification', { email }).catch(() => undefined);
    setResent(true);
  }

  return (
    <section className="narrow">
      <h1>Підтвердження e-mail</h1>
      {!state && <p>Перевірка посилання…</p>}
      {state && <p className={state.ok ? 'ok' : 'error'}>{state.text}</p>}
      {state?.expired && !resent && (
        <form onSubmit={resend} className="form">
          <label>
            Надіслати новий лист на адресу
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <button type="submit">Надіслати</button>
        </form>
      )}
      {resent && <p>Якщо адреса очікує підтвердження, новий лист уже в дорозі.</p>}
      <p>
        <Link to="/">На головну</Link>
      </p>
    </section>
  );
}

export function RegistrationClarificationPage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api<{ note: string }>(`/auth/registration-clarification/${encodeURIComponent(token)}`)
      .then((r) => setNote(r.note))
      .catch((err) => setError(errorText(err)));
  }, [token]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const doc = form.get('verification_document');
    if (doc instanceof File && doc.size === 0) form.delete('verification_document');
    try {
      await api(`/auth/registration-clarification/${encodeURIComponent(token)}`, { method: 'POST', body: form });
      setDone(true);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="narrow">
      <h1>Уточнення заявки на реєстрацію</h1>
      {error && <p className="error">{error}</p>}
      {done && <p className="ok">Відповідь передано адміністратору. Дякуємо!</p>}
      {note !== null && !done && (
        <form onSubmit={submit} className="form">
          <p>
            <strong>Питання адміністратора:</strong> {note}
          </p>
          <label>
            Ваша відповідь
            <textarea name="answer" required maxLength={2000} rows={4} />
          </label>
          <label>
            Документ (необов'язково; PDF, PNG або JPEG до 2 МБ)
            <input name="verification_document" type="file" accept=".pdf,.png,.jpg,.jpeg" />
          </label>
          <button type="submit">Надіслати</button>
        </form>
      )}
    </section>
  );
}
