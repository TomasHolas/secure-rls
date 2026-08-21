# uv's own image, not python:slim: pyproject pins python-preference = "only-managed", so the
# interpreter has to be a uv-managed build anyway - one interpreter in the image instead of two.
# Verified in this base: sqlite3 3.50.4 with enable_load_extension available, which sqlite-vec needs.
FROM ghcr.io/astral-sh/uv:bookworm-slim

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY .python-version pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

# Bake the read-only employee database from the committed CSV: deterministic, network-free, and
# the serving path only ever reads it (ADR 0003). vectors.db still needs live embeddings at runtime.
RUN uv run --no-sync python -c "import db; db.init_db(db.DEFAULT_DB_PATH.with_name('employees.csv'), db.DEFAULT_DB_PATH)"

# Non-root, and /app must stay writable: audit.db, state.db and checkpoints.db are written there.
RUN useradd --system --uid 10001 --create-home app && chown -R app:app /app
USER app

EXPOSE 8002

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["uv", "run", "--no-sync", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health').read()"]

CMD ["uv", "run", "--no-sync", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
