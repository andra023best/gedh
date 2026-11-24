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

// FR-35: запит посилання (однакова відповідь для будь-якого e-mail) і новий пароль за одноразовим посиланням (1 год).
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setSent((await post<{ message: string }>('/auth/password-reset/request', { email })).message);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="narrow">
      <h1>Відновлення пароля</h1>
      {sent ? (
        <p className="ok">{sent} Посилання дійсне 1 годину.</p>
      ) : (
        <form onSubmit={submit} className="form">
          <label>
            E-mail облікового запису
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">Надіслати посилання</button>
        </form>
      )}
      <p>
        <Link to="/login">Повернутися до входу</Link>
      </p>
    </section>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== repeat) return setState({ ok: false, text: 'Паролі не збігаються.' });
    try {
      const r = await post<{ message: string }>('/auth/password-reset/confirm', { token, password });
      setState({ ok: true, text: r.message });
    } catch (err) {
      setState({ ok: false, text: errorText(err) });
    }
  }

  return (
    <section className="narrow">
      <h1>Новий пароль</h1>
      {state?.ok ? (
        <p className="ok">
          {state.text} <Link to="/login">Увійти</Link>
        </p>
      ) : (
        <form onSubmit={submit} className="form">
          <label>
            Новий пароль (щонайменше 10 символів, літери й цифри)
            <input type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label>
            Повторіть пароль
            <input type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} />
          </label>
          {state && <p className="error">{state.text}</p>}
          <button type="submit">Зберегти пароль</button>
          <p className="hint">Після зміни пароля всі ваші сесії буде завершено — увійдіть знову.</p>
        </form>
      )}
    </section>
  );
}
