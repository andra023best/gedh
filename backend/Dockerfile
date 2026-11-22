# Образ для Render Free (512 МБ): один процес Uvicorn.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

RUN useradd --create-home appuser
USER appuser

# Під час кожного старту: міграції схеми й ідемпотентне заповнення довідників і демо-даних, потім API.
# Render передає порт у $PORT; --proxy-headers, бо запити приходять через проксі Render і Vercel.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed --demo && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
