# LineSense AI web image: builds the Vite/React SPA, then serves the static
# build behind Caddy, which also reverse-proxies /api and /auth to the
# backend and blocks /internal/* at the edge (see infra/proxy/Caddyfile).
#
# Build context is the repository root (see infra/compose/docker-compose.yml:
# services.web.build), because this image also needs infra/proxy/Caddyfile.
#
# Base image tags verified before pinning (2026-09-20):
#   curl -s https://hub.docker.com/v2/repositories/library/node/tags/26.9.0-alpine
#   curl -s https://hub.docker.com/v2/repositories/library/caddy/tags/2.11-alpine
# Recorded in docs/operations/deployment.md.
#
# Never built in the environment that authored this file (no Docker
# available); validated statically only (`make infra-check`).

# ---------------------------------------------------------------------------
# Stage 1: build — Node version pinned from apps/web/.nvmrc.
# ---------------------------------------------------------------------------
FROM node:26.9.0-alpine AS build

WORKDIR /web

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime — static files + reverse proxy, no Node in the final image.
# ---------------------------------------------------------------------------
FROM caddy:2.11-alpine AS runtime

COPY infra/proxy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv

EXPOSE 443
