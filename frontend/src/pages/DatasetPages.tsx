// UC-12 (постачальник: завантаження, DQ-звіт, підтвердження попереджень) і UC-13 (DG-офіцер: перевірка, публікація).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { blobFetch } from '../lib/download';
import { errorText } from '../lib/messages';
import { api, ApiError, post } from '../lib/session';
import { VERSION_STATUS_UK } from '../lib/datasetStatus';

type Issue = { rule_code: string; severity: 'CRITICAL' | 'WARNING'; record_ref: string; message: string };
export type Version = {
  id: string;
  dataset_id: string;
  dataset_title_ua: string | null;
  country_code: string | null;
  category_code: string | null;
  version_no: number;
  status: string;
  file_hash: string | null;
  created_at: string;
  published_at: string | null;
  rejection_reason: string | null;
  uploaded_by: string | null;
  records: number;
  source: Record<string, string | null> | null;
  quality_report: {
    records_total: number;
    critical_count: number;
    warning_count: number;
    score: number;
    warnings_confirmed_at: string | null;
    issues: Issue[];
  } | null;
  diff?: { previous_version_no: number; added: number; removed: number; changed: number };
};
type ProviderDataset = {
  id: string;
  title_ua: string;
  category_code: string;
  status: string;
  versions: { id: string; version_no: number; status: string; created_at: string }[];
};
type Category = { code: string; name_ua: string };

const SOURCE_UK: Record<string, string> = {
  document_title: 'Документ',
  document_number: 'Номер',
  document_date: 'Дата',
  url: 'Посилання',
  internal_ref: 'Внутрішній номер',
  methodology: 'Методика',
  responsible_person: 'Відповідальна особа',
};
const dt = (s: string | null) => (s ? new Date(s).toLocaleString('uk-UA') : '—');

export function VersionReport({ v }: { v: Version }) {
  const q = v.quality_report;
  return (
    <div className="card">
      <h3>
        {v.dataset_title_ua}, версія {v.version_no} — {VERSION_STATUS_UK[v.status] ?? v.status}
      </h3>
      <p className="hint">
        Країна {v.country_code}, категорія {v.category_code} · завантажив {v.uploaded_by ?? '—'} {dt(v.created_at)} ·
        записів {v.records} · SHA-256 <code>{v.file_hash?.slice(0, 16)}…</code>
      </p>
      {v.rejection_reason && <p className="error">Причина відхилення: {v.rejection_reason}</p>}
      {v.diff && (
        <p>
          Відмінності від версії {v.diff.previous_version_no}: додано {v.diff.added}, змінено {v.diff.changed}, вилучено{' '}
          {v.diff.removed}.
        </p>
      )}
      {v.source && (
        <>
          <h4>Першоджерело</h4>
          <dl className="kv">
            {Object.entries(v.source)
              .filter(([, val]) => val)
              .map(([k, val]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <dt>{SOURCE_UK[k] ?? k}</dt>
                  <dd>{k === 'url' ? <a href={val!} target="_blank" rel="noreferrer noopener">{val}</a> : val}</dd>
                </div>
              ))}
          </dl>
        </>
      )}
      {q && (
        <>
          <h4>
            DQ-звіт: оцінка {q.score} · критичних {q.critical_count} · попереджень {q.warning_count}
            {q.warnings_confirmed_at && ` (підтверджено ${dt(q.warnings_confirmed_at)})`}
          </h4>
          {q.issues.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Правило</th>
                  <th>Рівень</th>
                  <th>Запис</th>
                  <th>Опис</th>
                </tr>
              </thead>
              <tbody>
                {q.issues.map((i, n) => (
                  <tr key={n}>
                    <td>{i.rule_code}</td>
                    <td className={i.severity === 'CRITICAL' ? 'error' : 'warn'}>
                      {i.severity === 'CRITICAL' ? 'критичне' : 'попередження'}
                    </td>
                    <td>{i.record_ref}</td>
                    <td>{i.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

export function ProviderPage() {
  const qc = useQueryClient();
  const datasets = useQuery({ queryKey: ['provider-datasets'], queryFn: () => api<ProviderDataset[]>('/provider/datasets') });
  const categories = useQuery({ queryKey: ['categories'], queryFn: () => api<Category[]>('/categories') });
  const [target, setTarget] = useState('');
  const [result, setResult] = useState<Version | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState('');

  // Відкрити будь-яку версію: DQ-звіт, причина відхилення; для карантину — підтвердження попереджень пізніше
  async function openVersion(id: string) {
    setError(null);
    try {
      setResult(await api<Version>(`/dataset-versions/${id}`));
      setTimeout(() => document.getElementById('version-report')?.scrollIntoView({ behavior: 'smooth' }), 0);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setResult(null);
    const form = new FormData(e.currentTarget);
    if (target) {
      form.set('dataset_id', target);
      form.delete('title_ua');
      form.delete('title_en');
    }
    for (const [k, v] of [...form.entries()]) if (v === '') form.delete(k);
    setBusy(true);
    try {
      setResult(await api<Version>('/provider/uploads', { method: 'POST', body: form }));
      void qc.invalidateQueries({ queryKey: ['provider-datasets'] });
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === 'SOURCE_REQUIRED'
          ? 'Зазначте першоджерело: назву документа, методику, відповідальну особу й номер з датою, посилання або внутрішній реєстраційний номер.'
          : err instanceof ApiError && err.code === 'DUPLICATE_FILE'
            ? 'Цей файл уже завантажували (той самий SHA-256).'
            : errorText(err),
      );
    } finally {
      setBusy(false);
    }
  }

  const confirm = useMutation({
    mutationFn: (id: string) => post<Version>(`/dataset-versions/${id}/confirm-warnings`, { comment }),
    onSuccess: (v) => {
      setResult(v);
      void qc.invalidateQueries({ queryKey: ['provider-datasets'] });
    },
    onError: (err) => setError(errorText(err)),
  });

  return (
    <section>
      <h1>Мої набори даних</h1>
      {datasets.data?.length === 0 && <p>Наборів ще немає.</p>}
      {datasets.data && datasets.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Набір</th>
              <th>Категорія</th>
              <th>Версії</th>
            </tr>
          </thead>
          <tbody>
            {datasets.data.map((d) => (
              <tr key={d.id}>
                <td>{d.title_ua}</td>
                <td>{d.category_code}</td>
                <td>
                  {d.versions.map((v) => (
                    <div key={v.id}>
                      <button type="button" className="link" onClick={() => void openVersion(v.id)}>
                        v{v.version_no}: {VERSION_STATUS_UK[v.status] ?? v.status}
                      </button>
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Завантажити набір</h2>
      <p className="hint">
        Файл CSV (UTF-8) або JSON до 5 МБ лише за шаблоном:{' '}
        <button type="button" onClick={() => void blobFetch('/templates/upload.csv')}>
          шаблон CSV
        </button>{' '}
        ·{' '}
        <a href="/api/templates/upload.schema.json" target="_blank" rel="noreferrer">
          JSON Schema
        </a>
        . Країна — країна вашої організації. Усі закриті набори демо-платформи позначаються як синтетичні.
      </p>
      <form className="form" onSubmit={submit}>
        <label>
          Набір
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">новий набір</option>
            {datasets.data?.map((d) => (
              <option key={d.id} value={d.id}>
                нова версія: {d.title_ua}
              </option>
            ))}
          </select>
        </label>
        {!target && (
          <>
            <label>
              Категорія
              <select name="category_code" required defaultValue="">
                <option value="" disabled>
                  — оберіть —
                </option>
                {categories.data?.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name_ua}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Назва (UA)
              <input name="title_ua" required maxLength={300} />
            </label>
            <label>
              Назва (EN)
              <input name="title_en" required maxLength={300} />
            </label>
          </>
        )}
        {target && <input type="hidden" name="category_code" value="-" />}
        <label>
          Файл
          <input name="file" type="file" accept=".csv,.json" required />
        </label>
        <fieldset>
          <legend>Першоджерело (обов'язково)</legend>
          <label>
            Назва документа
            <input name="source_document_title" required maxLength={300} />
          </label>
          <label>
            Номер документа
            <input name="source_document_number" maxLength={100} />
          </label>
          <label>
            Дата документа
            <input name="source_document_date" type="date" />
          </label>
          <label>
            Посилання (https://…)
            <input name="source_url" type="url" maxLength={500} />
          </label>
          <label>
            Внутрішній реєстраційний номер
            <input name="source_internal_ref" maxLength={200} />
          </label>
          <label>
            Методика
            <textarea name="source_methodology" required rows={2} />
          </label>
          <label>
            Відповідальна особа
            <input name="source_responsible_person" required maxLength={200} />
          </label>
          <p className="hint">Потрібен хоча б один ідентифікатор: номер і дата, посилання або внутрішній номер.</p>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? 'Перевірка файлу…' : 'Завантажити й перевірити'}
        </button>
      </form>

      {result && (
        <div id="version-report">
          <VersionReport v={result} />
          {result.status === 'QUARANTINE' && (
            <div className="card">
              <p>Є лише попередження. Перегляньте їх і підтвердьте з коментарем — тоді набір піде на перевірку DG-офіцеру.</p>
              <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Коментар (≥ 5 символів)" />
              <p>
                <button disabled={comment.trim().length < 5 || confirm.isPending} onClick={() => confirm.mutate(result.id)}>
                  Підтвердити попередження
                </button>
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export function DgReviewPage() {
  const qc = useQueryClient();
  const queue = useQuery({ queryKey: ['dg-queue'], queryFn: () => api<Version[]>('/dg/review-queue') });
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ['version', selected],
    queryFn: () => api<Version>(`/dataset-versions/${selected}`),
    enabled: Boolean(selected),
  });
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post<Version>(`/dataset-versions/${selected}/${path}`, body),
    onSuccess: (v) => {
      setDone(`${v.dataset_title_ua}, версія ${v.version_no}: ${VERSION_STATUS_UK[v.status] ?? v.status}.`);
      setSelected(null);
      setReason('');
      void qc.invalidateQueries({ queryKey: ['dg-queue'] });
    },
    onError: (err) => setError(errorText(err)),
  });

  return (
    <section>
      <h1>Набори на перевірку</h1>
      {done && <p className="ok">{done}</p>}
      {queue.data?.length === 0 && <p>Черга порожня.</p>}
      {queue.data && queue.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Надійшло</th>
              <th>Набір</th>
              <th>Країна</th>
              <th>Записів</th>
              <th>Постачальник</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {queue.data.map((v) => (
              <tr key={v.id} className={v.id === selected ? 'selected' : undefined}>
                <td>{dt(v.created_at)}</td>
                <td>
                  {v.dataset_title_ua}, v{v.version_no}
                </td>
                <td>{v.country_code}</td>
                <td>{v.records}</td>
                <td>{v.uploaded_by}</td>
                <td>
                  <button
                    onClick={() => {
                      setSelected(v.id);
                      setError(null);
                      setDone(null);
                    }}
                  >
                    Перевірити
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {detail.data && (
        <>
          <VersionReport v={detail.data} />
          <div className="card">
            <p>Перевірте першоджерело (документ, методику, відповідальну особу) і DQ-звіт.</p>
            <button onClick={() => act.mutate({ path: 'publish' })} disabled={act.isPending}>
              Підтвердити й опублікувати
            </button>
            <p>
              <textarea rows={2} placeholder="Причина відхилення (≥ 5 символів)" value={reason} onChange={(e) => setReason(e.target.value)} />
            </p>
            <button
              className="danger"
              disabled={reason.trim().length < 5 || act.isPending}
              onClick={() => act.mutate({ path: 'reject', body: { reason: reason.trim() } })}
            >
              Відхилити
            </button>
            {error && <p className="error">{error}</p>}
          </div>
        </>
      )}
    </section>
  );
}
