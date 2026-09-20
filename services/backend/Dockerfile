# LineSense AI backend image: FastAPI (`api`), durable worker (`worker`), and
# a one-shot migration runner (`migrate`), selected by the container's first
# argument (see docker-entrypoint.sh).
#
# Base image tag verified against Docker Hub before pinning (2026-09-20):
#   curl -s https://hub.docker.com/v2/repositories/library/python/tags/3.12.14-slim-bookworm
# Recorded in docs/operations/deployment.md.
#
# Never built or run in the environment that authored this file (no Docker
# available); validated statically only (`make infra-check`).

# ---------------------------------------------------------------------------
# Stage 1: builder — resolve the frozen, non-dev dependency set with uv into
# a self-contained virtualenv.
# ---------------------------------------------------------------------------
FROM python:3.12.14-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# uv pinned to a specific PyPI release (verified 2026-09-20:
# https://pypi.org/pypi/uv/json -> info.version).
RUN pip install --no-cache-dir uv==0.12.17

WORKDIR /build
# Only the dependency manifests are needed to resolve the frozen lockfile;
# copying them separately from the application source keeps this layer
# cacheable across app-code-only changes.
COPY services/backend/pyproject.toml services/backend/uv.lock ./
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime — non-root, no dev tooling, no dev-only identity provider.
# ---------------------------------------------------------------------------
FROM python:3.12.14-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 linesense \
    && useradd --system --uid 10001 --gid linesense --home-dir /app --shell /usr/sbin/nologin linesense

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Application code, database migrations, and the Alembic config that ties
# them together — deliberately NOT `services/backend/devtools/` (the dev-only
# OIDC provider, ADR-0004) and not `tests/`.
COPY services/backend/app ./app
COPY services/backend/migrations ./migrations
COPY services/backend/alembic.ini ./alembic.ini

COPY services/backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R linesense:linesense /app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1

USER linesense
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# Overridden by docker-compose.yml per service: "api" | "worker" | "migrate".
CMD ["api"]
