// Каркас застосунку: меню за роллю, маршрути, банер «сервер прокидається», вікно нової версії згоди.
// Дизайн — окремим кроком (S-05); тут лише функціональні сторінки.
import { useEffect, useState } from 'react';
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { fetchWithWake, type WakeProgress } from './api';
import { RequireAuth } from './lib/auth';
import { useAuth } from './lib/authContext';
import { ROLE_UK } from './lib/messages';
import { api, logout, markConsentAccepted, post } from './lib/session';
import AdminRegistrationsPage from './pages/AdminRegistrationsPage';
import LoginPage from './pages/LoginPage';
import ProfilePage from './pages/ProfilePage';
import RegisterPage from './pages/RegisterPage';
import SystemPage from './pages/SystemPage';
import { RegistrationClarificationPage, VerifyEmailPage } from './pages/TokenPages';

function WakeBanner() {
  const [wake, setWake] = useState<WakeProgress | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    // Render Free засинає після 15 хв простою; перший запит «будить» сервер (нотатки, розд. 6)
    const timer = setTimeout(() => setWake({ attempt: 1, elapsedMs: 3000 }), 3000);
    fetchWithWake('/api/health', { onProgress: (p) => p.elapsedMs > 2500 && setWake(p) })
      .catch(() => setFailed(true))
      .finally(() => {
        clearTimeout(timer);
        setWake(null);
      });
    return () => clearTimeout(timer);
  }, []);
  if (failed) return <div className="banner error">Сервер недоступний. Спробуйте оновити сторінку за хвилину.</div>;
  if (!wake) return null;
  return (
    <div className="banner">
      Сервер прокидається після простою (безкоштовний хостинг), це триває до хвилини… {Math.round(wake.elapsedMs / 1000)}{' '}
      с
    </div>
  );
}

function ConsentModal() {
  const { user, consentRequired } = useAuth();
  const [consent, setConsent] = useState<{ text_version: string; text: string } | null>(null);
  useEffect(() => {
    if (consentRequired) void api<{ text_version: string; text: string }>('/auth/consent').then(setConsent);
  }, [consentRequired]);
  if (!user || !consentRequired || !consent) return null;
  // UC-03 AC2: текст згоди змінено — показати нову версію (DG-10)
  return (
    <div className="modal" role="dialog" aria-modal="true">
      <div className="modal-body">
        <h2>Оновлено текст згоди (версія {consent.text_version})</h2>
        <pre className="consent-text">{consent.text}</pre>
        <button onClick={() => void post('/me/consent', { text_version: consent.text_version }).then(markConsentAccepted)}>
          Приймаю
        </button>{' '}
        <Link to="/profile">Не приймаю — перейти до видалення облікового запису</Link>
      </div>
    </div>
  );
}

function Nav() {
  const { user } = useAuth();
  const navigate = useNavigate();
  return (
    <header className="nav">
      <Link to="/" className="brand">
        GEDH
      </Link>
      <nav>
        {user?.role === 'ADMIN' && <NavLink to="/admin/registrations">Заявки на реєстрацію</NavLink>}
        {user && <NavLink to="/profile">Профіль</NavLink>}
        <NavLink to="/system">Стан системи</NavLink>
      </nav>
      <div className="who">
        {user ? (
          <>
            {user.full_name} · {ROLE_UK[user.role]}{' '}
            <button
              onClick={() => {
                navigate('/'); // спершу йдемо з захищеної сторінки, потім завершуємо сесію
                void logout();
              }}
            >
              Вийти
            </button>
          </>
        ) : (
          <>
            <NavLink to="/login">Увійти</NavLink> · <NavLink to="/register">Реєстрація</NavLink>
          </>
        )}
      </div>
    </header>
  );
}

function HomePage() {
  const { user } = useAuth();
  const notice = (useLocation().state as { notice?: string } | null)?.notice;
  return (
    <section>
      {notice && <p className="ok">{notice}</p>}
      <h1>Global Economic Data Hub</h1>
      <p>
        Платформа керованого обміну макроекономічними даними: відкриті показники World Bank, IMF і BIS для 25 країн та
        закриті набори постачальників, доступні лише за погодженою заявкою й угодою.
      </p>
      <p className="hint">
        Навчальний проєкт групи ОІ-34. Закриті набори на платформі — <strong>синтетичні дані</strong>, не офіційна
        статистика.
      </p>
      {!user && (
        <p>
          <Link to="/register">Зареєструватися</Link> або <Link to="/login">увійти</Link>.
        </p>
      )}
      <p className="hint">Карта, каталог і профілі країн з'являться на наступних етапах.</p>
    </section>
  );
}

export default function App() {
  return (
    <>
      <WakeBanner />
      <Nav />
      <ConsentModal />
      <main>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/verify-email" element={<VerifyEmailPage />} />
          <Route path="/registration-clarification" element={<RegistrationClarificationPage />} />
          <Route
            path="/profile"
            element={
              <RequireAuth>
                <ProfilePage />
              </RequireAuth>
            }
          />
          <Route
            path="/admin/registrations"
            element={
              <RequireAuth roles={['ADMIN']}>
                <AdminRegistrationsPage />
              </RequireAuth>
            }
          />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<p>Сторінку не знайдено.</p>} />
        </Routes>
      </main>
    </>
  );
}
