# Calton — single-container image: FastAPI serves the API and the built React bundle.
#
# Build context is the repository root, not server/:
#     docker build -f server/Dockerfile -t calton .

FROM node:24-alpine AS frontend
WORKDIR /web
COPY web-react/package.json web-react/package-lock.json ./
RUN npm ci
COPY web-react/ ./
RUN npm run build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first so the layer survives source edits.
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server/src ./src
COPY server/migrations ./migrations
COPY server/alembic.ini ./
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

RUN useradd --create-home --uid 1000 calton

COPY --from=builder --chown=calton:calton /app /app
COPY --from=frontend --chown=calton:calton /web/dist /app/src/calton/static

ENV PATH="/app/.venv/bin:$PATH" \
    CALTON_DATABASE_PATH=/data/calton.db \
    CALTON_FILES_BASEPATH=/data/files

RUN mkdir -p /data && chown calton:calton /data
VOLUME /data
USER calton
EXPOSE 3456

CMD ["uvicorn", "calton.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "3456"]
