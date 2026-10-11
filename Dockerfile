FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    TESSERACT_CMD=/usr/bin/tesseract \
    OMP_THREAD_LIMIT=1 \
    OCR_MAX_CONCURRENT_JOBS=1 \
    HTTP_MAX_CONCURRENT_UPLOADS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-ind \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --list-langs | grep -Fx eng \
    && tesseract --list-langs | grep -Fx ind

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

# Explicit copies keep local files and Render secret files outside image layers.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker/entrypoint.py ./docker/entrypoint.py

# Render secret files are readable by group 1000. Application code stays root-owned.
RUN groupadd --gid 1000 vire \
    && useradd --uid 10001 --gid vire --create-home --shell /usr/sbin/nologin vire \
    && mkdir -p /app/uploads \
    && chown vire:vire /app/uploads

USER vire

EXPOSE 10000
STOPSIGNAL SIGTERM

# Migrations are an operator step, never a container startup side effect.
CMD ["python", "/app/docker/entrypoint.py"]
