// Аналітика відкритих даних (Е6б): карта покриття (UC-04), профіль країни (UC-05), порівняння й рейтинг (UC-06),
// картка показника (FR-09). Закриті дані тут не показуються — лише перелік наборів «доступ за заявкою» (UC-05 AC2).
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import LineChart, { type Series } from '../components/LineChart';
import WorldMap, { type MapCountry } from '../components/WorldMap';
import { useAuth } from '../lib/authContext';
import { fmtValue } from '../lib/geo';
import { errorText } from '../lib/messages';
import { api } from '../lib/session';

type Coverage = { country: string; indicators: number; codes: string[] }[];
type Named = { code: string; name_ua: string; iso_numeric?: string | null };
type IndicatorMeta = {
  code: string;
  name_ua: string;
  name_en: string;
  definition: string;
  unit: string;
  scale: string;
  frequency: string;
};
type CatalogDs = {
  id: string;
  kind: string;
  title_ua: string;
  source_system: string | null;
  licence: string;
  indicators: string[];
  country_code: string | null;
  category_code: string | null;
  owner: { name: string } | null;
  is_synthetic: boolean;
  current_version_no: number | null;
  updated_at: string | null;
};
type SeriesPoint = { period: string; start: string; value: number | null; note: string | null };
type ProfileIndicator = IndicatorMeta & {
  dataset_id: string;
  dataset_title: string;
  version_no: number;
  published_at: string | null;
  source: string | null;
  licence: string;
  series: SeriesPoint[];
  latest: { period: string; value: number; note: string | null } | null;
  previous: { period: string; value: number } | null;
  change: number | null;
};
type Profile = {
  country: { code: string; name_ua: string; name_en: string };
  indicators: ProfileIndicator[];
  restricted: CatalogDs[];
};
type Obs = { items: { country: string; indicator: string; period: { start: string }; value: number | null; note: string | null }[] };

const FREQ_UK: Record<string, string> = { ANNUAL: 'щорічно', QUARTERLY: 'щокварталу', MONTHLY: 'щомісяця' };
const SOURCE_UK: Record<string, string> = {
  WB: 'World Bank — World Development Indicators',
  IMF: 'IMF — World Economic Outlook',
  BIS: 'BIS — Central bank policy rates',
};
const MAX_COUNTRIES = 4; // UC-06 EX1, FR-10: дві–чотири країни
const d = (s: string | null) => (s ? new Date(s).toLocaleDateString('uk-UA') : '—');

const useCountries = () => useQuery({ queryKey: ['countries'], queryFn: () => api<Named[]>('/countries') });
const useIndicators = () => useQuery({ queryKey: ['indicators'], queryFn: () => api<IndicatorMeta[]>('/indicators') });
const useCatalog = () => useQuery({ queryKey: ['catalog'], queryFn: () => api<CatalogDs[]>('/catalog/datasets') });

// ------------------------------------------------------------------------------------------------ карта (UC-04)
export function CoverageMap() {
  const navigate = useNavigate();
  const coverage = useQuery({ queryKey: ['coverage'], queryFn: () => api<Coverage>('/coverage') });
  const countries = useCountries();
  const [picked, setPicked] = useState<{ code: string | null; name: string } | null>(null);

  const map = useMemo(() => {
    const byCode = Object.fromEntries((countries.data ?? []).map((c) => [c.code, c]));
    const out: Record<string, MapCountry> = {};
    for (const c of coverage.data ?? [])
      out[c.country] = {
        code: c.country,
        name: byCode[c.country]?.name_ua ?? c.country,
        indicators: c.indicators,
        numeric: byCode[c.country]?.iso_numeric,
      };
    return out;
  }, [coverage.data, countries.data]);

  if (coverage.error) return <p className="error">{errorText(coverage.error)}</p>;
  if (!coverage.data || !countries.data) return <p className="hint">Завантаження карти…</p>;

  function select(code: string | null, name: string) {
    if (code) return navigate(`/countries/${code}`); // UC-04 крок 5: до профілю країни
    setPicked({ code, name }); // сіра країна — повідомлення (EX1)
  }

  return (
    <div>
      <WorldMap countries={map} onSelect={select} selected={picked?.code} />
      <p className="hint">
        Зелені — {Object.keys(map).length} країн з відкритими даними World Bank, IMF і BIS. Натисніть на
        країну, щоб відкрити її профіль.
      </p>
      {picked && !picked.code && <p className="warn">Для країни «{picked.name}» даних на платформі немає.</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ профіль (UC-05)
export function CountryPage() {
  const { code = '' } = useParams();
  const { user } = useAuth();
  const profile = useQuery({
    queryKey: ['profile', code],
    queryFn: () => api<Profile>(`/countries/${code}/profile`),
    refetchOnWindowFocus: false, // кожен перегляд — запис у журнал
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');

  if (profile.error) return <p className="error">{errorText(profile.error)}</p>;
  if (!profile.data) return <p>Завантаження…</p>;
  const { country, indicators, restricted } = profile.data;
  const current = indicators.find((i) => i.code === selected) ?? indicators[0];
  const periods = current ? current.series.map((p) => p.period) : [];
  const shown = current ? current.series.filter((p) => (!from || p.period >= from) && (!to || p.period <= to)) : [];

  return (
    <section>
      <h1>{country.name_ua}</h1>
      <p className="hint">
        {country.name_en} · {country.code} ·{' '}
        <Link to={`/compare?countries=${country.code}${current ? `&indicator=${current.code}` : ''}`}>Порівняти з іншими країнами</Link>
      </p>
      {indicators.length === 0 && <p className="warn">Для цієї країни відкритих даних ще немає.</p>}

      <div className="cards">
        {indicators.map((i) => (
          <button
            key={i.code}
            className={`stat-card${i.code === current?.code ? ' active' : ''}`}
            onClick={() => setSelected(i.code)}
          >
            <span className="stat-name">{i.name_ua}</span>
            <span className="stat-value">{fmtValue(i.latest?.value, i.unit, i.scale)}</span>
            <span className="hint">
              {i.latest?.period ?? '—'}
              {i.change !== null && (
                <span className={i.change >= 0 ? 'up' : 'down'}>
                  {' '}
                  {i.change >= 0 ? '▲' : '▼'} {fmtValue(Math.abs(i.change), '', '')} за рік
                </span>
              )}
              {i.latest?.note && ' *'}
            </span>
          </button>
        ))}
      </div>

      {current && (
        <div className="card">
          <h2>
            {current.name_ua}, {current.unit}
            {current.scale && !['одиниці', 'частка'].includes(current.scale) ? ` (${current.scale})` : ''}
          </h2>
          <p>
            Період:{' '}
            <select value={from} onChange={(e) => setFrom(e.target.value)}>
              <option value="">з початку</option>
              {periods.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>{' '}
            —{' '}
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">до кінця</option>
              {periods.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </p>
          <LineChart
            series={[{ label: country.name_ua, points: shown.map((p) => ({ x: p.period, y: p.value })) }]}
            format={(v) => fmtValue(v)}
          />
          <p className="hint">
            Джерело: {current.source ?? '—'} · оновлено {d(current.published_at)} · версія {current.version_no} · ліцензія{' '}
            {current.licence}. Пропуски — «н/д», значення не інтерполюються. ·{' '}
            <Link to={`/indicators/${current.code}`}>Про показник</Link> ·{' '}
            <Link to={`/datasets/${current.dataset_id}/lineage`}>Походження</Link>
          </p>
          <table>
            <thead>
              <tr>
                <th>Період</th>
                <th>Значення</th>
                <th>Примітка</th>
              </tr>
            </thead>
            <tbody>
              {[...shown].reverse().map((p) => (
                <tr key={p.period}>
                  <td>{p.period}</td>
                  <td>{fmtValue(p.value, current.unit, current.scale)}</td>
                  <td className="hint">{p.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Закриті набори країни</h2>
      {restricted.length === 0 && <p>Закритих наборів для цієї країни немає.</p>}
      {restricted.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Набір</th>
              <th>Постачальник</th>
              <th>Доступ</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {restricted.map((r) => (
              <tr key={r.id}>
                <td>
                  {r.title_ua}
                  {r.is_synthetic && <div className="synthetic">СИНТЕТИЧНІ ДАНІ</div>}
                </td>
                <td>{r.owner?.name ?? '—'}</td>
                <td>доступ за заявкою</td>
                <td>
                  {user?.role === 'ORG_REPRESENTATIVE' && (
                    <Link to={`/requests?country=${r.country_code}&category=${r.category_code ?? ''}`}>Подати заявку</Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ порівняння (UC-06)
export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const countries = useCountries();
  const indicators = useIndicators();
  const catalog = useCatalog();
  const [limitMsg, setLimitMsg] = useState(false);

  const picked = (params.get('countries') ?? '').split(',').filter(Boolean);
  const mode = params.get('mode') === 'rank' ? 'rank' : 'chart';
  const openDs = (catalog.data ?? []).filter((x) => x.kind === 'OPEN' && x.current_version_no);
  const available = (indicators.data ?? []).filter((i) => openDs.some((x) => x.indicators.includes(i.code)));
  const indicator = params.get('indicator') ?? available[0]?.code ?? '';
  const meta = available.find((i) => i.code === indicator);
  const ds = openDs.find((x) => x.indicators.includes(indicator));
  const names = Object.fromEntries((countries.data ?? []).map((c) => [c.code, c.name_ua]));

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };
  const addCountry = (c: string) => {
    if (!c || picked.includes(c)) return;
    if (picked.length >= MAX_COUNTRIES) return setLimitMsg(true); // UC-06 EX1
    setLimitMsg(false);
    set({ countries: [...picked, c].join(',') });
  };

  // графік — лише обрані країни; рейтинг — усі країни (один запит, записується як перегляд даних)
  const obs = useQuery({
    queryKey: ['compare', ds?.id, mode, mode === 'chart' ? picked.join(',') : 'all'],
    enabled: !!ds && (mode === 'rank' || picked.length > 0),
    refetchOnWindowFocus: false,
    queryFn: () =>
      api<Obs>(`/datasets/${ds!.id}/observations${mode === 'chart' ? `?countries=${picked.join(',')}` : ''}`),
  });
  const items = (obs.data?.items ?? []).filter((o) => o.indicator === indicator);
  const years = [...new Set(items.map((o) => o.period.start.slice(0, 4)))].sort();
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  const inRange = (y: string) => (!from || y >= from) && (!to || y <= to);

  const series: Series[] = picked.map((c) => ({
    label: names[c] ?? c,
    points: items
      .filter((o) => o.country === c && inRange(o.period.start.slice(0, 4)))
      .map((o) => ({ x: o.period.start.slice(0, 4), y: o.value })),
  }));
  const rankYear = params.get('year') ?? years.filter((y) => items.some((o) => o.period.start.startsWith(y) && o.value !== null)).at(-1) ?? '';
  const ranking = items
    .filter((o) => o.period.start.startsWith(rankYear) && o.value !== null)
    .sort((a, b) => (b.value as number) - (a.value as number));
  const maxAbs = Math.max(...ranking.map((r) => Math.abs(r.value as number)), 1);

  return (
    <section>
      <h1>Порівняння країн</h1>
      <div className="filters">
        <label>
          Показник
          <select value={indicator} onChange={(e) => set({ indicator: e.target.value, year: null })}>
            {available.map((i) => (
              <option key={i.code} value={i.code}>
                {i.name_ua}
              </option>
            ))}
          </select>
        </label>
        <label>
          Режим
          <select
            value={mode}
            onChange={(e) => {
              setLimitMsg(false);
              set({ mode: e.target.value === 'rank' ? 'rank' : null });
            }}
          >
            <option value="chart">графік для 2–4 країн</option>
            <option value="rank">рейтинг усіх країн за рік</option>
          </select>
        </label>
        {mode === 'chart' && (
          <>
            <label>
              Додати країну ({picked.length} з {MAX_COUNTRIES})
              <select value="" onChange={(e) => addCountry(e.target.value)}>
                <option value="">оберіть…</option>
                {(countries.data ?? [])
                  .filter((c) => !picked.includes(c.code))
                  .map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name_ua}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Від
              <select value={from} onChange={(e) => set({ from: e.target.value })}>
                <option value="">з початку</option>
                {years.map((y) => (
                  <option key={y}>{y}</option>
                ))}
              </select>
            </label>
            <label>
              До
              <select value={to} onChange={(e) => set({ to: e.target.value })}>
                <option value="">до кінця</option>
                {years.map((y) => (
                  <option key={y}>{y}</option>
                ))}
              </select>
            </label>
          </>
        )}
        {mode === 'rank' && (
          <label>
            Рік
            <select value={rankYear} onChange={(e) => set({ year: e.target.value })}>
              {years.map((y) => (
                <option key={y}>{y}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {mode === 'chart' && (
        <p>
          {picked.map((c) => (
            <span key={c} className="chip">
              {names[c] ?? c}{' '}
              <button aria-label={`Прибрати ${names[c] ?? c}`} onClick={() => set({ countries: picked.filter((x) => x !== c).join(',') })}>
                ×
              </button>
            </span>
          ))}
        </p>
      )}
      {limitMsg && <p className="warn">Можна порівняти не більше {MAX_COUNTRIES} країн: так графік лишається читабельним (UC-06).</p>}
      {obs.error && <p className="error">{errorText(obs.error)}</p>}

      {meta && (
        <p className="hint">
          {meta.name_ua}: {meta.unit}
          {meta.scale && !['одиниці', 'частка'].includes(meta.scale) ? `, ${meta.scale}` : ''}; {FREQ_UK[meta.frequency]}. Джерело:{' '}
          {SOURCE_UK[ds?.source_system ?? ''] ?? ds?.source_system}. <Link to={`/indicators/${meta.code}`}>Про показник</Link>
        </p>
      )}

      {mode === 'chart' && picked.length === 0 && <p>Додайте хоча б одну країну.</p>}
      {mode === 'chart' && picked.length > 0 && obs.data && <LineChart series={series} format={(v) => fmtValue(v)} />}

      {mode === 'rank' && obs.data && (
        <div className="ranking">
          <p className="hint">
            {rankYear} рік, {ranking.length} країн з даними.
          </p>
          <ol>
            {ranking.map((r) => (
              <li key={r.country} className={picked.includes(r.country) ? 'picked' : undefined}>
                <Link to={`/countries/${r.country}`} className="rank-name">
                  {names[r.country] ?? r.country}
                </Link>
                <span className="bar-track">
                  <span
                    className={`bar${(r.value as number) < 0 ? ' neg' : ''}`}
                    style={{ width: `${(Math.abs(r.value as number) / maxAbs) * 100}%` }}
                  />
                </span>
                <span className="rank-value">{fmtValue(r.value, meta?.unit, meta?.scale)}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ картка показника (FR-09)
type Card = IndicatorMeta & {
  classification: string | null;
  source: string | null;
  licence: string | null;
  datasets: CatalogDs[];
  coverage: { country: string; from: string; to: string }[];
};

export function IndicatorPage() {
  const { code = '' } = useParams();
  const { user } = useAuth();
  const card = useQuery({ queryKey: ['indicator', code], queryFn: () => api<Card>(`/indicators/${code}`) });
  const countries = useCountries();
  const names = Object.fromEntries((countries.data ?? []).map((c) => [c.code, c.name_ua]));
  if (card.error) return <p className="error">{errorText(card.error)}</p>;
  if (!card.data) return <p>Завантаження…</p>;
  const c = card.data;
  return (
    <section>
      <h1>{c.name_ua}</h1>
      <dl className="kv">
        <dt>Код</dt>
        <dd>
          <code>{c.code}</code>
        </dd>
        <dt>Назва англійською</dt>
        <dd>{c.name_en}</dd>
        <dt>Визначення</dt>
        <dd>{c.definition}</dd>
        <dt>Одиниця й масштаб</dt>
        <dd>
          {c.unit}, {c.scale}
        </dd>
        <dt>Частота</dt>
        <dd>{FREQ_UK[c.frequency] ?? c.frequency}</dd>
        <dt>Джерело</dt>
        <dd>{c.source ? (SOURCE_UK[c.source] ?? c.source) : 'постачальники даних країн (закриті набори)'}</dd>
        <dt>Класифікація</dt>
        <dd>{c.classification === 'OPEN' ? 'відкриті дані' : c.classification === 'RESTRICTED' ? 'закриті — доступ за заявкою' : '—'}</dd>
        <dt>Ліцензія</dt>
        <dd>{c.licence ?? '—'}</dd>
      </dl>
      <h2>Набори</h2>
      <ul>
        {c.datasets.map((x) => (
          <li key={x.id}>
            {x.title_ua}
            {x.is_synthetic && <span className="synthetic"> СИНТЕТИЧНІ ДАНІ</span>} · версія {x.current_version_no ?? '—'} · оновлено{' '}
            {d(x.updated_at)}
            {user && (
              <>
                {' '}
                · <Link to={`/datasets/${x.id}/lineage`}>Походження</Link>
              </>
            )}
          </li>
        ))}
      </ul>
      {c.coverage.length > 0 && (
        <>
          <h2>Покриття ({c.coverage.length} країн)</h2>
          <p>
            {user ? (
              <Link to={`/compare?indicator=${c.code}&mode=rank`}>Рейтинг країн за цим показником</Link>
            ) : (
              <>
                Значення — після <Link to="/login">входу</Link>.
              </>
            )}
          </p>
          <table>
            <thead>
              <tr>
                <th>Країна</th>
                <th>Дані за</th>
              </tr>
            </thead>
            <tbody>
              {[...c.coverage]
                .sort((a, b) => (names[a.country] ?? a.country).localeCompare(names[b.country] ?? b.country, 'uk'))
                .map((r) => (
                <tr key={r.country}>
                  <td>{user ? <Link to={`/countries/${r.country}`}>{names[r.country] ?? r.country}</Link> : (names[r.country] ?? r.country)}</td>
                  <td>
                    {r.from} — {r.to}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
