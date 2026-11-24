// Походження набору (FR-33, DG-16): першоджерело → імпорт / завантаження → перевірка якості → версія →
// дозволи → вивантаження. Чужі дозволи й вивантаження бачать лише DG-офіцер і організація-власник.
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { GRANT_STATUS_UK, VERSION_STATUS_UK } from '../lib/datasetStatus';
import { errorText, ROLE_UK } from '../lib/messages';
import { api } from '../lib/session';

type Source = Record<string, string | null>;
type Export = { at: string; format: string; records: number; file_sha256: string; grant_id: string | null; organization: string | null };
type Version = {
  version_no: number;
  status: string;
  created_at: string;
  source: Source;
  file_sha256: string | null;
  uploaded_by_role: string | null;
  quality: { score: number; records: number; critical: number; warnings: number; warnings_confirmed_at: string | null } | null;
  published_at: string | null;
  published_by_role: string | null;
  rejection_reason: string | null;
  previous_version_no: number | null;
  exports: Export[];
};
type GrantRow = {
  id: string;
  organization: string | null;
  period: [string, string];
  valid_from: string;
  valid_to: string;
  status: string;
  suspension_reason: string | null;
  deletion_confirmed_at: string | null;
};
type Lineage = {
  dataset: { title_ua: string; kind: string; is_synthetic: boolean; licence: string; source_system: string | null };
  scope: 'all' | 'own';
  versions: Version[];
  grants: GrantRow[];
};

const dt = (s: string | null) => (s ? new Date(s).toLocaleString('uk-UA') : '—');
const d = (s: string | null) => (s ? new Date(s).toLocaleDateString('uk-UA') : '—');
const short = (h: string | null) => (h ? `${h.slice(0, 12)}…` : '—');
const role = (r: string | null) => (r ? (ROLE_UK[r] ?? r) : 'система');

export default function LineagePage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ['lineage', id], queryFn: () => api<Lineage>(`/datasets/${id}/lineage`) });
  if (q.error) return <p className="error">{errorText(q.error)}</p>;
  if (!q.data) return <p>Завантаження…</p>;
  const { dataset, versions, grants, scope } = q.data;
  const open = dataset.kind === 'OPEN';

  return (
    <section>
      <h1>Походження: {dataset.title_ua}</h1>
      {dataset.is_synthetic && <p className="synthetic">СИНТЕТИЧНІ ДАНІ</p>}
      <p className="hint">
        Ланцюжок DG-16: першоджерело → {open ? 'імпорт з API' : 'завантаження постачальником'} → перевірка якості → версія
        → дозволи → вивантаження. Ліцензія: {dataset.licence}.
        {scope === 'own' && ' Дозволи й вивантаження показано лише ваші.'}
      </p>

      {versions.length === 0 && <p>Версій поки немає.</p>}
      {[...versions].reverse().map((v) => (
        <div key={v.version_no} className="card">
          <h2>
            Версія {v.version_no} — {VERSION_STATUS_UK[v.status] ?? v.status}
            {v.previous_version_no && <span className="hint"> (замінила версію {v.previous_version_no})</span>}
          </h2>
          <ol className="lineage">
            <li>
              <strong>Першоджерело:</strong> {v.source.document_title ?? '—'}
              {v.source.document_number && `, № ${v.source.document_number}`}
              {v.source.document_date && ` від ${d(v.source.document_date)}`}
              {v.source.url && (
                <div className="hint">
                  <a href={v.source.url} target="_blank" rel="noreferrer">
                    {v.source.url.length > 90 ? `${v.source.url.slice(0, 90)}…` : v.source.url}
                  </a>
                </div>
              )}
              <div className="hint">
                Методика: {v.source.methodology ?? '—'}; відповідальна особа: {v.source.responsible_person ?? '—'}
              </div>
            </li>
            <li>
              <strong>{open ? 'Імпорт' : 'Завантаження'}:</strong> {dt(v.created_at)}
              {!open && ` (${role(v.uploaded_by_role)})`}; хеш {open ? 'сирої відповіді' : 'файлу'} SHA-256:{' '}
              <code title={(open ? v.source.raw_sha256 : v.file_sha256) ?? ''}>
                {short(open ? v.source.raw_sha256 : v.file_sha256)}
              </code>
            </li>
            <li>
              <strong>Перевірка якості:</strong>{' '}
              {v.quality
                ? `DQ-score ${v.quality.score}; записів ${v.quality.records}, критичних ${v.quality.critical}, попереджень ${v.quality.warnings}`
                : '—'}
              {v.quality?.warnings_confirmed_at && ` (попередження підтверджено ${d(v.quality.warnings_confirmed_at)})`}
            </li>
            <li>
              <strong>Рішення:</strong>{' '}
              {v.published_at
                ? `опубліковано ${dt(v.published_at)} (${role(v.published_by_role)})`
                : v.rejection_reason
                  ? `відхилено: ${v.rejection_reason}`
                  : 'не опубліковано'}
            </li>
            <li>
              <strong>Вивантаження:</strong> {v.exports.length === 0 && 'немає'}
              {v.exports.length > 0 && (
                <ul>
                  {v.exports.map((x) => (
                    <li key={x.at + x.file_sha256}>
                      {dt(x.at)} · {x.format.toUpperCase()} · {x.records} записів · {x.organization ?? '—'}
                      {x.grant_id ? ' · за дозволом' : ''} · SHA-256 <code title={x.file_sha256}>{short(x.file_sha256)}</code>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          </ol>
        </div>
      ))}

      {!open && (
        <>
          <h2>Дозволи на набір</h2>
          {grants.length === 0 && <p>Дозволів немає.</p>}
          {grants.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Організація</th>
                  <th>Період даних</th>
                  <th>Діє</th>
                  <th>Статус</th>
                  <th>Видалення підтверджено</th>
                </tr>
              </thead>
              <tbody>
                {grants.map((g) => (
                  <tr key={g.id}>
                    <td>{g.organization}</td>
                    <td>
                      {d(g.period[0])} — {d(g.period[1])}
                    </td>
                    <td>
                      {d(g.valid_from)} — {d(g.valid_to)}
                    </td>
                    <td>
                      {GRANT_STATUS_UK[g.status] ?? g.status}
                      {g.suspension_reason && <div className="hint">{g.suspension_reason}</div>}
                    </td>
                    <td>{d(g.deletion_confirmed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      <p>
        <Link to="/catalog">До каталогу</Link>
      </p>
    </section>
  );
}
