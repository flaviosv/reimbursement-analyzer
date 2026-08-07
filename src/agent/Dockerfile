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
# layer — the real install happens after the source COPY below.
COPY pyproject.toml uv.lock ./
COPY src/agent/pyproject.toml src/agent/pyproject.toml
# uv's --locked check (below) validates against the whole workspace, so the
# sibling manifests need to exist even though only agent's source is copied —
# their *source* never enters this image, only these tiny manifest files.
COPY src/api/pyproject.toml src/api/pyproject.toml
COPY src/publisher/pyproject.toml src/publisher/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package agent

COPY src/agent src/agent
COPY src/shared src/shared

# Editable: the venv links back to /app/src/agent instead of baking the code
# in, so the dev stage below can bind-mount live source over it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package agent

# --- dev: editable install, hot reload, meant to run with src/ bind-mounted ---
FROM python:3.14.7-slim AS dev
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
USER app
ENV PATH="/app/.venv/bin:$PATH"

# agent.consumer is a plain script (no framework-level reload like uvicorn),
# so watchfiles restarts the process on any change under /app/src.
CMD ["watchfiles", "python -m agent.consumer", "/app/src"]

# --- prod: code baked in non-editable, no reload, runs standalone ---
FROM builder AS prod-builder
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package agent

FROM python:3.14.7-slim AS prod
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=prod-builder --chown=app:app /app/.venv /app/.venv
USER app
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "agent.consumer"]
