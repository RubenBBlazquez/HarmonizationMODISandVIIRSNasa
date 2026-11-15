FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/backend/requirements.txt

COPY backend/etls/pyproject.toml /app/backend/etls/pyproject.toml
COPY backend/etls/fire_etl /app/backend/etls/fire_etl
RUN pip install /app/backend/etls

COPY backend/__init__.py /app/backend/__init__.py
COPY backend/api /app/backend/api
COPY backend/main.py /app/backend/main.py

EXPOSE 8000

# Railway (y otros PaaS) inyectan PORT; en local se usa 8000. --proxy-headers porque en producción
# la API queda detrás del proxy de Railway y del Caddy del frontend.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
