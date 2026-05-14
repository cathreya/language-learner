FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# uv binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Install deps first (layer cache).
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# App code.
COPY app ./app
COPY scripts ./scripts
COPY README.md ./

# Cloud Run listens on $PORT (defaults to 8080). The HOST=0.0.0.0 is required so
# the container accepts traffic from the Cloud Run proxy.
ENV PORT=8080 \
    HOST=0.0.0.0

EXPOSE 8080

# We don't bind the data dir to anything on Cloud Run — captures.db lives in
# Turso, audio in GCS, .apkg builds in /tmp. DATA_DIR is only for the
# legacy local-dev path.
ENV DATA_DIR=/tmp/data

CMD ["sh", "-c", "uv run --no-dev uvicorn app.main:app --host ${HOST} --port ${PORT}"]
