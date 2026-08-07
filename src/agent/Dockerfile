# Build context is the repo root (uv workspace), not this directory —
# see the `build.context` / `build.dockerfile` pair in docker-compose.yml.

FROM python:3.14.7-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# --no-install-workspace skips local packages entirely at this stage (incl.
# shared), so only agent's own manifest is needed for this deps-only cache
# layer — the real install (with the full repo) happens after COPY . . below.
COPY pyproject.toml uv.lock ./
COPY src/agent/pyproject.toml src/agent/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package agent

COPY . .

# Editable (not --no-editable): the venv links back to /app/src/agent instead
# of baking the code in, so bind-mounting live source over /app/src in
# docker-compose.yml is picked up without rebuilding the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package agent

FROM python:3.14.7-slim
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# agent.consumer is a plain script (no framework-level reload like uvicorn),
# so watchfiles restarts the process on any change under /app/src.
CMD ["watchfiles", "python -m agent.consumer", "/app/src"]
