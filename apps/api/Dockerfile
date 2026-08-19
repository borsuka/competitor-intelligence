# Multi-stage: dependencies are resolved once in the builder and copied into a runtime
# image that carries no compiler toolchain.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Build-only dependencies for wheels that have no manylinux build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Copy the package before installing so setuptools can resolve it.
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq for asyncpg's runtime, curl for container healthchecks. No compilers.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 sentinel

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=sentinel:sentinel app ./app
COPY --chown=sentinel:sentinel alembic ./alembic
COPY --chown=sentinel:sentinel alembic.ini pyproject.toml ./

# Non-root: a crawler that fetches attacker-supplied URLs should not run as root.
USER sentinel

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
