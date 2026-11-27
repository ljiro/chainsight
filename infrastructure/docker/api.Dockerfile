# Backend image: used by the api, worker, beat, and migrate services.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

# Dependencies first for layer caching. uv.lock is optional until it is committed.
COPY pyproject.toml uv.lock* README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -f uv.lock ]; then uv sync --locked --no-dev --no-install-project; \
    else uv sync --no-dev --no-install-project; fi

COPY core ./core
COPY database ./database
COPY integrations ./integrations
COPY services ./services
COPY ml ./ml
COPY apps/api ./apps/api
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -f uv.lock ]; then uv sync --locked --no-dev; else uv sync --no-dev; fi

# Mount point for the shared ML volume; a fresh named volume inherits this ownership.
RUN mkdir -p /app/data/models && chown -R app:app /app/data

# Recorded on research runs (code_version) for reproducibility; set by CI.
ARG GIT_SHA=
ENV CHAINSIGHT_GIT_SHA=$GIT_SHA

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "chainsight_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
