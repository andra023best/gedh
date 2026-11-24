// UC-03 «Увійти в систему» (FR-05).
import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { errorText } from '../lib/messages';
import { login } from '../lib/session';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/data';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const t = await login(email.trim(), password);
      const home: Record<string, string> = {
        ADMIN: '/admin/registrations',
        DATA_PROVIDER: '/provider',
        DG_OFFICER: '/dg/review',
        ORG_REPRESENTATIVE: '/requests',
      };
      // Адресу чужого розділу, запам'ятовану після виходу іншого користувача, не використовуємо
      const foreign = ['/admin', '/provider', '/dg', '/requests', '/grants'].some((p) => from.startsWith(p));
      const target = home[t.user.role] ?? (foreign ? '/data' : from);
      navigate(target, { replace: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="narrow">
      <h1>Вхід</h1>
      <form onSubmit={submit} className="form">
        <label>
          E-mail
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Пароль
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? 'Вхід…' : 'Увійти'}
        </button>
      </form>
      <p>
        Немає облікового запису? <Link to="/register">Зареєструватися</Link>
      </p>
      <p>
        <Link to="/forgot-password">Забули пароль?</Link>
      </p>
    </section>
  );
}
