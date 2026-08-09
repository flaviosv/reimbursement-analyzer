# Build context is the repo root (uv workspace), not this directory —
# see the `build.context` / `build.dockerfile` pair in docker-compose.yml.

FROM python:3.14.7-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# --no-install-workspace skips local packages entirely at this stage (incl.
# shared), so only reimbursement's own manifest is needed for this deps-only
# cache layer — the real install happens after the source COPY below.
COPY pyproject.toml uv.lock ./
COPY src/reimbursement/pyproject.toml src/reimbursement/pyproject.toml
# uv's --locked check (below) validates against the whole workspace, so the
# sibling manifests need to exist even though only reimbursement's source is
# copied — their *source* never enters this image, only these manifests.
COPY src/api/pyproject.toml src/api/pyproject.toml
COPY src/publisher/pyproject.toml src/publisher/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package reimbursement

COPY src/reimbursement src/reimbursement
COPY src/shared src/shared

# reimbursement is a virtual workspace member (package = false in its
# pyproject.toml — src/reimbursement/src holds loose modules, not a package
# uv_build can wheel up), so there is nothing to (re-)install here beyond its
# already-synced dependencies; every stage below runs reimbursement straight
# from source via PYTHONPATH instead of an installed import.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package reimbursement

# --- dev: hot reload, meant to run with src/ bind-mounted ---
FROM python:3.14.7-slim AS dev
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src/reimbursement/src"

# consumer is a plain script (no framework-level reload like uvicorn), so
# watchfiles restarts the process on any change under /app/src.
CMD ["watchfiles", "python -m consumer", "/app/src"]

# --- prod: same source, no reload, runs standalone ---
FROM builder AS prod-builder
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package reimbursement

FROM python:3.14.7-slim AS prod
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=prod-builder --chown=app:app /app/.venv /app/.venv
COPY --from=prod-builder --chown=app:app /app/src /app/src
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src/reimbursement/src"

CMD ["python", "-m", "consumer"]
