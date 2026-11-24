// Каталог без значень (UC-04 AC1, L0 — для всіх) і таблиця значень відкритого показника (L1 — після входу).
// Тимчасовий вигляд до етапу Е6 (карта, профіль країни, графіки) і дизайну (S-05).
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../lib/authContext';
import { errorText } from '../lib/messages';
import { blobFetch } from '../lib/download';
import { api } from '../lib/session';

type Dataset = {
  id: string;
  kind: 'OPEN' | 'RESTRICTED';
  title_ua: string;
  classification: string;
  licence: string;
  is_synthetic: boolean;
  source_system: string | null;
  country_code: string | null;
  category_code: string | null;
  owner: { name: string } | null;
  indicators: string[];
  current_version_no: number | null;
  updated_at: string | null;
};
type Country = { code: string; name_ua: string };
type Observations = {
  title_ua: string;
  version_no: number;
  published_at: string;
  licence: string;
  is_synthetic: boolean;
  quality: { score: number; warnings: number } | null;
  source: { document_title?: string; url?: string; methodology?: string };
  items: { country: string; period: { start: string }; value: number | null; note: string | null }[];
};

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleDateString('uk-UA') : '—');
const fmt = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 2 });
// 10 завершених років до поточного (поточний рік ще не має річних значень)
const LAST_TEN_YEARS = Array.from({ length: 10 }, (_, i) => new Date().getFullYear() - 10 + i);

function useCatalog() {
  return useQuery({ queryKey: ['catalog'], queryFn: () => api<Dataset[]>('/catalog/datasets') });
}

export function CatalogPage() {
  const catalog = useCatalog();
  const { user } = useAuth();
  if (catalog.isLoading) return <p>Завантаження…</p>;
  if (catalog.error) return <p className="error">{errorText(catalog.error)}</p>;
  const open = catalog.data!.filter((d) => d.kind === 'OPEN');
  const closed = catalog.data!.filter((d) => d.kind === 'RESTRICTED');
  return (
    <section>
      <h1>Каталог даних</h1>
      <p className="hint">
        Опис наборів без значень доступний усім. Значення відкритих показників — після реєстрації
        {user ? (
          <>
            : <Link to="/data">переглянути дані</Link>.
          </>
        ) : (
          '.'
        )}
      </p>
      <h2>Відкриті показники (World Bank, IMF, BIS)</h2>
      <table>
        <thead>
          <tr>
            <th>Показник</th>
            <th>Джерело</th>
            <th>Ліцензія</th>
            <th>Версія</th>
            <th>Оновлено</th>
          </tr>
        </thead>
        <tbody>
          {open.map((d) => (
            <tr key={d.id}>
              <td>
                {d.indicators[0] ? <Link to={`/indicators/${d.indicators[0]}`}>{d.title_ua}</Link> : d.title_ua}
                {user && (
                  <>
                    {' '}
                    · <Link to={`/datasets/${d.id}/lineage`}>походження</Link>
                  </>
                )}
              </td>
              <td>{d.source_system}</td>
              <td>{d.licence}</td>
              <td>{d.current_version_no ?? 'ще не імпортовано'}</td>
              <td>{fmtDate(d.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Закриті набори — доступ лише за заявкою</h2>
      <table>
        <thead>
          <tr>
            <th>Набір</th>
            <th>Країна</th>
            <th>Постачальник</th>
            <th>Позначка</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {closed.map((d) => (
            <tr key={d.id}>
              <td>{d.title_ua}</td>
              <td>{d.country_code}</td>
              <td>{d.owner?.name ?? '—'}</td>
              <td>{d.is_synthetic && <strong className="synthetic">СИНТЕТИЧНІ ДАНІ</strong>}</td>
              <td>
                {user && <Link to={`/datasets/${d.id}/lineage`}>Походження</Link>}{' '}
                {user?.role === 'ORG_REPRESENTATIVE' && d.kind === 'RESTRICTED' && (
                  <Link to={`/requests?country=${d.country_code}&category=${d.category_code ?? ''}`}>Подати заявку</Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function OpenDataPage() {
  const catalog = useCatalog();
  const countries = useQuery({
    queryKey: ['countries'],
    queryFn: async () => (await api<Country[]>('/countries')).sort((a, b) => a.name_ua.localeCompare(b.name_ua, 'uk')),
  });
  const open = (catalog.data ?? []).filter((d) => d.kind === 'OPEN' && d.current_version_no);
  const [selected, setSelected] = useState<string>('');
  const datasetId = selected || open[0]?.id || '';
  const obs = useQuery({
    queryKey: ['observations', datasetId],
    queryFn: () => api<Observations>(`/datasets/${datasetId}/observations`),
    enabled: Boolean(datasetId),
  });

  if (catalog.isLoading) return <p>Завантаження…</p>;
  if (!open.length) return <p>Відкриті дані ще не імпортовано.</p>;

  const years = LAST_TEN_YEARS;
  const cell = new Map<string, { value: number | null; note: string | null }>();
  obs.data?.items.forEach((o) => cell.set(`${o.country}:${o.period.start.slice(0, 4)}`, o));

  return (
    <section>
      <h1>Відкриті дані</h1>
      <label>
        Показник{' '}
        <select value={datasetId} onChange={(e) => setSelected(e.target.value)}>
          {open.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title_ua}
            </option>
          ))}
        </select>
      </label>
      {obs.isLoading && <p>Завантаження…</p>}
      {obs.error && <p className="error">{errorText(obs.error)}</p>}
      {obs.data && (
        <>
          <p className="hint">
            Джерело: {obs.data.source.document_title} · ліцензія {obs.data.licence} · версія {obs.data.version_no} від{' '}
            {fmtDate(obs.data.published_at)}
            {obs.data.quality && ` · якість даних ${obs.data.quality.score} (попереджень: ${obs.data.quality.warnings})`}
          </p>
          <div className="scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Країна</th>
                  {years.map((y) => (
                    <th key={y}>{y}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {countries.data?.map((c) => (
                  <tr key={c.code}>
                    <td>{c.name_ua}</td>
                    {years.map((y) => {
                      const v = cell.get(`${c.code}:${y}`);
                      return (
                        <td key={y} className="num" title={v?.note ?? undefined}>
                          {v?.value != null ? fmt.format(v.value) : <span className="na">н/д</span>}
                          {v?.note && '*'}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            <button onClick={() => void blobFetch(`/datasets/${datasetId}/export?format=csv`)}>Вивантажити CSV</button>{' '}
            <button onClick={() => void blobFetch(`/datasets/${datasetId}/export?format=json`)}>Вивантажити JSON</button>
          </p>
          <p className="hint">
            «н/д» — джерело не має значення; інтерполяція не виконується. * — примітка (наведіть курсор), наприклад
            оцінка IMF або ставка ЄЦБ для країн єврозони.
          </p>
        </>
      )}
    </section>
  );
}
