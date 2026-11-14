FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/backend/requirements.txt

COPY backend/etls/src/pyproject.toml /app/backend/etls/src/pyproject.toml
COPY backend/etls/src/fire_etl /app/backend/etls/src/fire_etl
RUN pip install /app/backend/etls/src

COPY backend/main.py /app/backend/main.py

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
