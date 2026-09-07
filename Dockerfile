# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────────────────────
# Reproducibility contract for this image
#
#   * Both base images are pinned by DIGEST, not by tag. Tags are mutable; a tag
#     pin means two builds of the same commit can silently disagree.
#   * Node is 22.22.0, matching the machine convention and the frontend lane in
#     .github/workflows/required-merge-gate.yml. This image previously built the
#     frontend on node:20-alpine while CI type-checked it on 22.22.0, so the
#     artifact was never built by the toolchain that gated it.
#   * Python dependencies install from a fully-pinned, hash-verified lock with
#     --require-hashes. Any artifact whose content does not match is refused.
#   * There is no apt-get layer. Debian package versions are not pinned by the
#     old `apt-get install gcc libpq-dev curl nodejs npm` line, so that layer was
#     the single largest source of build-to-build drift. Each package was checked
#     before removal — see docs/SUPPLY-CHAIN.md "Removed system packages".
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: frontend build ──
FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: python runtime ──
FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93 AS runtime

# Provenance, supplied by the build and recorded on the artifact itself so a
# running container can be traced back to a commit without a side channel.
ARG CORVUS_REVISION=unknown
ARG CORVUS_VERSION=0.0.0-dev
ARG CORVUS_BUILD_ID=unknown
LABEL org.opencontainers.image.title="corvus-mind" \
      org.opencontainers.image.revision="${CORVUS_REVISION}" \
      org.opencontainers.image.version="${CORVUS_VERSION}" \
      org.opencontainers.image.source="https://github.com/tylerbvogel-max/corvus" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Deterministic Python behaviour. PYTHONHASHSEED fixes str/bytes hash ordering,
# which otherwise varies per process and can reorder any set-derived output.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Python deps, hash-verified. requirements.txt is generated from pyproject.toml
# by backend/scripts/lock_dependencies.py — never hand-edited.
COPY backend/requirements.txt ./
RUN pip install --require-hashes --no-deps -r requirements.txt

# Application code
COPY backend/pyproject.toml ./
COPY backend/app/ ./app/
COPY backend/tenants/ ./tenants/
COPY backend/alembic/ ./alembic/
COPY backend/alembic.ini ./
COPY backend/scripts/start_backend.sh ./scripts/start_backend.sh
COPY architecture/architecture.json architecture/conformance.json ./architecture/

# Built frontend. The previous INCLUDE_FRONTEND ARG was declared twice and never
# consulted — this COPY ran unconditionally — so the flag advertised a choice the
# build did not actually offer. Removed rather than left as decoration.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Non-root runtime user.
RUN useradd --create-home --shell /bin/bash corvus \
    && chown -R corvus:corvus /app
USER corvus
ENV HOME=/home/corvus

ENV PORT=8005
ENV TENANT_ID=corvus-mind
ENV CORVUS_BIND_HOST=0.0.0.0
ENV CORVUS_FRONTEND_DIST=/app/frontend/dist
ENV CORVUS_ARCHITECTURE_DIR=/app/architecture

# NOTE: CLAUDE_CLI_PATH is deliberately NOT set here.
#
# It used to be baked as /root/.config/nvm/versions/node/v20.20.0/bin/claude,
# which was wrong three separate ways: the image runs as `corvus` and cannot read
# /root, the image never installed that CLI, and the path pinned a Node version
# this build no longer uses. The product direction is a cloud graph with a
# user-supplied brain and zero server-side LLM spend, so the artifact ships with
# no provider baked in. Operators opt in explicitly — see docs/SUPPLY-CHAIN.md.
EXPOSE ${PORT}

# Healthcheck via the interpreter that is already present, so the image does not
# need curl (and therefore does not need an unpinned apt layer to get it).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8005')+'/health',timeout=4).status==200 else 1)"]

CMD ["/bin/bash", "./scripts/start_backend.sh"]
