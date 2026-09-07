"""Every metric object owned by `api`, plus the scrape-time DB refresh
function for the one Gauge (`reimbursement_status_count`).

Every metric object here is a module-level constant, constructed once at
import time — never per-request/per-call — so re-importing this module
never raises `prometheus_client`'s duplicate-registration error."""

import logging

import asyncpg
from prometheus_client import Counter, Gauge, Histogram
from shared.config import load_config
from shared.reimbursement import repository

logger = logging.getLogger(__name__)

# The fixed literal for a 404's `path` label (spec.md Assumption 7) — keeps
# `path` cardinality bounded to the app's actual route table instead of the
# attacker/client-controlled, otherwise-unbounded raw request path.
UNMATCHED_PATH_LABEL = "unmatched"

# The 6 values from 0001.create-reimbursement.sql's CHECK constraint
# (AD-003) — used to zero-fill the Gauge so a status with 0 current rows
# still reports 0, not an absent series.
ALL_STATUSES: tuple[str, ...] = (
    "pending",
    "human-review",
    "auto-approved",
    "auto-rejected",
    "human-approved",
    "human-rejected",
)

# Constructed without the `_total` suffix — prometheus_client appends it at
# exposition time (spec.md's Counter naming rule).
api_http_requests_total = Counter(
    "api_http_requests",
    "Every HTTP request handled by the API, including error responses",
    labelnames=["method", "path", "status_code"],
)

# prometheus_client's own default HTTP-latency-shaped buckets — appropriate
# for an API layer with no LLM calls in its own request path.
api_http_request_duration_seconds = Histogram(
    "api_http_request_duration_seconds",
    "HTTP request handling latency",
    labelnames=["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

reimbursement_status_count = Gauge(
    "reimbursement_status_count",
    "Current number of reimbursement requests in each status, refreshed each scrape via a DB query",
    labelnames=["status"],
)

# 1 minute to 1 week — the "minutes-to-days" scale spec Assumption 3
# mandates, deliberately sharing no bucket list with any `reimbursement`-side
# histogram.
reimbursement_review_wait_seconds = Histogram(
    "reimbursement_review_wait_seconds",
    "Time an item spent in human-review before a reviewer resolved it",
    buckets=(60, 300, 900, 3600, 14400, 43200, 86400, 259200, 604800),
)


async def refresh_status_gauge(pool: asyncpg.Pool) -> None:
    """Refreshes `reimbursement_status_count` from the current DB state at
    scrape time — not incrementally maintained in memory. On any failure
    (e.g. the DB is unreachable), logs one warning and returns without
    raising: a metrics scrape is not a reimbursement decision, so the rest
    of `/metrics` should still return the other 15 metrics rather than the
    whole endpoint failing over one query (spec.md Assumption 6)."""
    try:
        async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
            rows = await repository.count_by_status(conn)
    except Exception:
        logger.warning("failed to refresh reimbursement_status_count gauge", exc_info=True)
        return

    counts = {row["status"]: row["count"] for row in rows}
    for status in ALL_STATUSES:
        reimbursement_status_count.labels(status).set(counts.get(status, 0))
