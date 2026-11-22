FROM python:3.12-slim

# uv binary from the official image, pinned to the version that created uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Dependencies only. This layer is reused until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

# 2) The project itself.
COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY generator ./generator
COPY samples/truth/operators.csv ./samples/truth/operators.csv
RUN uv sync --locked --no-dev

# Run as an unprivileged user. Files stay owned by root, so the app cannot modify itself.
RUN useradd --create-home --uid 10001 app && install -d -o app -g app /app/scenario-data
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "inspection_trace.main:app", "--host", "0.0.0.0", "--port", "8000"]
