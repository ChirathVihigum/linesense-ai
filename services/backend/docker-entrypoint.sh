#!/usr/bin/env sh
# Entrypoint for the LineSense backend image. Selects one of three runtime
# modes from the container's first argument; everything else is passed
# through as extra arguments to the underlying command.
#
# api      — uvicorn serving app.main:app. --proxy-headers is restricted to
#            LS_FORWARDED_ALLOW_IPS (default 127.0.0.1, i.e. trust nothing
#            unless explicitly configured) so only the reverse proxy's own
#            network can set X-Forwarded-* headers (see
#            infra/proxy/Caddyfile and infra/compose/docker-compose.yml).
# worker   — the durable job worker (`python -m app.jobs`).
# migrate  — one-shot `alembic upgrade head`, using LS_MIGRATION_DATABASE_URL
#            (the linesense_owner role). Intended to run to completion and
#            exit; see the `migrate` service in docker-compose.yml.
set -eu

mode="${1:-}"
[ $# -gt 0 ] && shift

case "$mode" in
  api)
    exec uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --proxy-headers \
      --forwarded-allow-ips "${LS_FORWARDED_ALLOW_IPS:-127.0.0.1}" \
      "$@"
    ;;
  worker)
    exec python -m app.jobs --concurrency "${LS_WORKER_CONCURRENCY:-4}" "$@"
    ;;
  migrate)
    exec alembic upgrade head "$@"
    ;;
  *)
    echo "Usage: docker-entrypoint.sh {api|worker|migrate} [extra args...]" >&2
    exit 1
    ;;
esac
