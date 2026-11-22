# GEDH — Global Economic Data Hub

Платформа керованого дата-шерингу для аналізу світової економіки. Командний проєкт з дисципліни «Проєктування інформаційних систем», група ОІ-34:

- Щербина Андрій — керівник проєкту, frontend, логіка Data Governance;
- Шабловський Тимофій — архітектор, backend і база даних;
- Шалайко Павло — бізнес-аналітик, тестування.

> **Синтетичні дані.** Закриті набори на платформі згенеровані для демонстрації й не є реальною статистикою.

## Стан

Етап **Е0** — каркас і перевірка хостингу. Сторінка сайту зараз показує діагностику ланцюжка «браузер → Vercel → Render → Neon»; бізнес-функцій ще немає.

## Структура

| Папка | Що там |
|---|---|
| `backend/` | API на FastAPI (Python 3.13), PostgreSQL через SQLAlchemy 2 і psycopg 3 |
| `frontend/` | React + TypeScript + Vite |
| `docs/` | контракт API та інша технічна документація |
| `render.yaml` | налаштування Render (backend, Франкфурт, безкоштовний тариф) |
| `frontend/vercel.json` | Vercel: проксі `/api/*` на Render і маршрутизація SPA |

## Локальний запуск

Backend (PowerShell, з папки `backend`):

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Frontend (з папки `frontend`, в іншому терміналі):

```powershell
npm install
npm run dev
```

Сайт відкривається на http://localhost:5173, документація API — на http://localhost:8000/api/docs.

## Перевірки

| Що | Команда |
|---|---|
| тести backend | `.\.venv\Scripts\python -m pytest` |
| стиль backend | `.\.venv\Scripts\python -m ruff check .` і `ruff format --check .` |
| тести frontend | `npm test` |
| стиль і збірка frontend | `npm run lint`, `npm run build` |

GitHub Actions виконує всі ці перевірки на кожен push і pull request.

## Секрети

Паролі й рядки підключення — лише в налаштуваннях Render, Vercel і GitHub Secrets. У репозиторії є тільки `backend/.env.example` без значень.
