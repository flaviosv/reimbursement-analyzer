from typing import Annotated

from fastapi import Query

from shared.errors import ReimbursementFilterInvalid

# Type coercion only, no ge/le bounds — the pagination/status semantics are
# use_cases.list_reimbursements' job, not this extraction layer's. Defaults
# are supplied at each call site (`= 100`, `= 0`), not embedded in Query()
# here — FastAPI requires the former (a bare default inside
# Annotated[..., Query(default=...)] raises at import time). No StatusQuery
# alias: a repeated `?status=a&status=b` must be rejected (LIST-08), which
# needs the route's raw multi-value query params, not a FastAPI-bound scalar
# that would silently collapse to one value — see parse_status_filter below.
LimitQuery = Annotated[int, Query()]
OffsetQuery = Annotated[int, Query()]


def parse_status_filter(values: list[str]) -> list[str] | None:
    """Take the raw multi-value `status` query params: reject a repeated
    `?status=a&status=b` (LIST-08/AD-028), then split the single remaining
    value on ',' into unchecked segments; None when absent or empty.
    No whitelist check here — that's the use case's gate."""
    if len(values) > 1:
        raise ReimbursementFilterInvalid("status must be supplied once, comma-separated for multiple values")
    if not values or not values[0]:
        return None
    return values[0].split(",")
