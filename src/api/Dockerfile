# Build context is the repo root (uv workspace), not this directory —
# see the `build.context` / `build.dockerfile` pair in docker-compose.yml.

FROM python:3.14.7-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# --no-install-workspace skips local packages entirely at this stage (incl.
# shared), so only api's own manifest is needed for this deps-only cache
# layer — the real install (with the full repo) happens after COPY . . below.
COPY pyproject.toml uv.lock ./
COPY src/api/pyproject.toml src/api/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package api

COPY . .

# Editable (not --no-editable): the venv links back to /app/src/api instead
# of baking the code in, so bind-mounting live source over /app/src in
# docker-compose.yml is picked up without rebuilding the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package api

FROM python:3.14.7-slim
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]
