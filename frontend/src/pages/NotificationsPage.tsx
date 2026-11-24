// Сповіщення в інтерфейсі (FR-29): зміни статусу заявок, наборів і дозволів. Той самий текст приходить листом (FR-39).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { errorText } from '../lib/messages';
import { api, post } from '../lib/session';

export type Notice = {
  id: string;
  kind: string;
  title: string;
  text: string;
  link: string | null;
  created_at: string;
  read_at: string | null;
};
export type Feed = { items: Notice[]; unread: number };

export default function NotificationsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const feed = useQuery({ queryKey: ['notifications'], queryFn: () => api<Feed>('/notifications?limit=100') });
  const done = () => void qc.invalidateQueries({ queryKey: ['notifications'] });
  const readAll = useMutation({ mutationFn: () => post('/notifications/read-all'), onSuccess: done });

  async function open(n: Notice) {
    if (!n.read_at) await post(`/notifications/${n.id}/read`).catch(() => undefined);
    done();
    if (n.link) navigate(n.link);
  }

  return (
    <section>
      <h1>Сповіщення</h1>
      {feed.error && <p className="error">{errorText(feed.error)}</p>}
      {feed.data && feed.data.items.length === 0 && <p>Сповіщень поки немає.</p>}
      {feed.data && feed.data.unread > 0 && (
        <p>
          <button onClick={() => readAll.mutate()}>Позначити всі прочитаними</button>
        </p>
      )}
      <ul className="notices">
        {feed.data?.items.map((n) => (
          <li key={n.id} className={n.read_at ? undefined : 'unread'}>
            <div>
              <strong>{n.title}</strong> <span className="hint">{new Date(n.created_at).toLocaleString('uk-UA')}</span>
            </div>
            <div className="pre">{n.text}</div>
            <button onClick={() => void open(n)}>{n.link ? 'Відкрити' : 'Позначити прочитаним'}</button>
          </li>
        ))}
      </ul>
      <p className="hint">Сповіщення зберігаються 180 днів. Значень закритих даних у них немає (DG-25).</p>
    </section>
  );
}
