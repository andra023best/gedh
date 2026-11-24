// Каркас застосунку: меню за роллю, маршрути, банер «сервер прокидається», вікно нової версії згоди.
// Дизайн — окремим кроком (S-05); тут лише функціональні сторінки.
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { fetchWithWake, type WakeProgress } from './api';
import { RequireAuth } from './lib/auth';
import { useAuth } from './lib/authContext';
import { ROLE_UK } from './lib/messages';
import { api, logout, markConsentAccepted, post } from './lib/session';
import AboutPage, { AboutContent } from './pages/AboutPage';
import { ConnectorsPage, ReferencePage } from './pages/AdminConfigPages';
import AdminRegistrationsPage from './pages/AdminRegistrationsPage';
import { ComparePage, CountryPage, CoverageMap, IndicatorPage } from './pages/AnalyticsPages';
import AuditPage from './pages/AuditPage';
import { CatalogPage, OpenDataPage } from './pages/DataPages';
import { DgReviewPage, ProviderPage } from './pages/DatasetPages';
import { GrantsPage, RequestsPage } from './pages/ExchangePages';
import LineagePage from './pages/LineagePage';
import LoginPage from './pages/LoginPage';
import NotificationsPage, { type Feed } from './pages/NotificationsPage';
import ProfilePage from './pages/ProfilePage';
import RegisterPage from './pages/RegisterPage';
import SystemPage from './pages/SystemPage';
import {
  ForgotPasswordPage,
  RegistrationClarificationPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from './pages/TokenPages';

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

// FR-29: лічильник непрочитаних сповіщень; оновлюється раз на хвилину, поки вкладка відкрита
function NotificationsLink() {
  const feed = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api<Feed>('/notifications?limit=100'),
    refetchInterval: 60_000,
  });
  const n = feed.data?.unread ?? 0;
  return <NavLink to="/notifications">Сповіщення{n > 0 ? ` (${n})` : ''}</NavLink>;
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
        {!user && <NavLink to="/about">Про платформу</NavLink>}
        {user && <NavLink to="/catalog">Каталог</NavLink>}
        {user && <NavLink to="/data">Дані</NavLink>}
        {user && <NavLink to="/compare">Порівняння</NavLink>}
        {user && ['ORG_REPRESENTATIVE', 'ADMIN', 'DATA_PROVIDER'].includes(user.role) && (
          <NavLink to="/requests">Заявки на дані</NavLink>
        )}
        {user && user.role !== 'ANALYST' && <NavLink to="/grants">Дозволи</NavLink>}
        {user?.role === 'DATA_PROVIDER' && <NavLink to="/provider">Мої набори</NavLink>}
        {user?.role === 'DG_OFFICER' && <NavLink to="/dg/review">Перевірка наборів</NavLink>}
        {user?.role === 'DG_OFFICER' && <NavLink to="/audit">Журнал аудиту</NavLink>}
        {user?.role === 'ADMIN' && <NavLink to="/admin/registrations">Заявки на реєстрацію</NavLink>}
        {user?.role === 'ADMIN' && <NavLink to="/admin/connectors">Конектори</NavLink>}
        {user?.role === 'ADMIN' && <NavLink to="/admin/reference">Довідники</NavLink>}
        {user && <NotificationsLink />}
        {user && <NavLink to="/profile">Профіль</NavLink>}
        {user?.role === 'ADMIN' && <NavLink to="/system">Стан системи</NavLink>}
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
  // S-36: гостю — лише опис платформи й реєстрація; карта, каталог і дані — після входу
  if (!user)
    return (
      <section>
        {notice && <p className="ok">{notice}</p>}
        <h1>Global Economic Data Hub</h1>
        <AboutContent />
        <p>
          <Link to="/register">Зареєструватися</Link> або <Link to="/login">увійти</Link>.
        </p>
      </section>
    );
  return (
    <section>
      {notice && <p className="ok">{notice}</p>}
      <h1>Global Economic Data Hub</h1>
      <p className="hint">
        Відкриті показники World Bank, IMF і BIS для 25 країн. Закриті набори — лише за заявкою й угодою; на платформі
        вони <strong>синтетичні</strong>. <Link to="/catalog">Каталог даних</Link> · <Link to="/about">Про платформу</Link>
      </p>
      <h2>Карта покриття</h2>
      <CoverageMap />
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
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route
            path="/notifications"
            element={
              <RequireAuth>
                <NotificationsPage />
              </RequireAuth>
            }
          />
          <Route
            path="/datasets/:id/lineage"
            element={
              <RequireAuth>
                <LineagePage />
              </RequireAuth>
            }
          />
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
          <Route path="/about" element={<AboutPage />} />
          <Route
            path="/admin/connectors"
            element={
              <RequireAuth roles={['ADMIN']}>
                <ConnectorsPage />
              </RequireAuth>
            }
          />
          <Route
            path="/admin/reference"
            element={
              <RequireAuth roles={['ADMIN']}>
                <ReferencePage />
              </RequireAuth>
            }
          />
          <Route
            path="/catalog"
            element={
              <RequireAuth>
                <CatalogPage />
              </RequireAuth>
            }
          />
          <Route
            path="/indicators/:code"
            element={
              <RequireAuth>
                <IndicatorPage />
              </RequireAuth>
            }
          />
          <Route
            path="/countries/:code"
            element={
              <RequireAuth>
                <CountryPage />
              </RequireAuth>
            }
          />
          <Route
            path="/compare"
            element={
              <RequireAuth>
                <ComparePage />
              </RequireAuth>
            }
          />
          <Route
            path="/data"
            element={
              <RequireAuth>
                <OpenDataPage />
              </RequireAuth>
            }
          />
          <Route
            path="/provider"
            element={
              <RequireAuth roles={['DATA_PROVIDER']}>
                <ProviderPage />
              </RequireAuth>
            }
          />
          <Route
            path="/dg/review"
            element={
              <RequireAuth roles={['DG_OFFICER']}>
                <DgReviewPage />
              </RequireAuth>
            }
          />
          <Route
            path="/audit"
            element={
              <RequireAuth roles={['DG_OFFICER']}>
                <AuditPage />
              </RequireAuth>
            }
          />
          <Route
            path="/requests"
            element={
              <RequireAuth roles={['ORG_REPRESENTATIVE', 'ADMIN', 'DATA_PROVIDER']}>
                <RequestsPage />
              </RequireAuth>
            }
          />
          <Route
            path="/grants"
            element={
              <RequireAuth roles={['ORG_REPRESENTATIVE', 'ADMIN', 'DATA_PROVIDER', 'DG_OFFICER']}>
                <GrantsPage />
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
