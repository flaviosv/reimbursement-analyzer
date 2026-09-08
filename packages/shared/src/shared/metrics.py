"""Cross-service metrics: the one metric genuinely owned by two processes
(`reimbursement_status_transitions_total`), plus the `start_metrics_server()`
helper both `publisher` and `reimbursement` call once at startup.

Every metric object here is a module-level constant, constructed once at
import time — never per-request/per-call — so re-importing this module never
raises `prometheus_client`'s duplicate-registration error."""

import logging
import threading
from wsgiref.simple_server import WSGIServer

from prometheus_client import Counter, start_http_server

logger = logging.getLogger(__name__)

# Constructed without the `_total` suffix — prometheus_client appends it at
# exposition time (spec.md's Counter naming rule).
# `from` is a Python reserved word: every call site MUST use positional
# `.labels(from_value, to_value)`, matching this declared labelnames order
# exactly. `.labels(from=..., to=...)` is a SyntaxError and must never be
# attempted.
reimbursement_status_transitions_total = Counter(
    "reimbursement_status_transitions",
    "Every observed reimbursement status transition, wherever it happens",
    labelnames=["from", "to"],
)


def start_metrics_server(port: int) -> tuple[WSGIServer, threading.Thread]:
    """Starts a dedicated Prometheus metrics HTTP server on `port`, serving
    `/metrics` off the process's default registry. Raises on bind failure
    (e.g. port already in use) rather than swallowing it — consistent with
    `check_startup_config()`'s existing fail-fast-at-boot convention in both
    `publisher` and `reimbursement`. Returns the underlying (server, thread)
    pair so a caller that needs to tear it down — a test, chiefly, since
    production entrypoints let it run for the life of the process — can call
    `server.shutdown()` / `server.server_close()` instead of leaking the
    socket and thread."""
    server, thread = start_http_server(port)
    logger.info("metrics server listening on port %s", port)
    return server, thread
