# Stage 1: Build frontend (optional — for demo UI)
ARG INCLUDE_FRONTEND=true
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + optional frontend
FROM python:3.11-slim AS runtime
WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/app/ ./app/
COPY backend/tenants/ ./tenants/
COPY backend/alembic/ ./alembic/
COPY backend/alembic.ini ./

# Built frontend (conditional)
ARG INCLUDE_FRONTEND=true
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Non-root user for security
RUN useradd --create-home --shell /bin/bash corvus
USER corvus

# Default env (overridden by docker-compose)
ENV PORT=8005
ENV TENANT_ID=corvus-mind
ENV CLAUDE_CLI_PATH=/root/.config/nvm/versions/node/v20.20.0/bin/claude

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:${PORT}/health || exit 1

CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}