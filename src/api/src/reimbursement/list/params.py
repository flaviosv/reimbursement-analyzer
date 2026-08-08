from typing import Annotated

from fastapi import Query

# Type coercion only, no ge/le bounds — the pagination/status semantics are
# use_cases.list_reimbursements' job, not this extraction layer's. Defaults
# are supplied at each call site (`= 100`, `= 0`, `= None`), not embedded in
# Query() here — FastAPI requires the former (a bare default inside
# Annotated[..., Query(default=...)] raises at import time).
LimitQuery = Annotated[int, Query()]
OffsetQuery = Annotated[int, Query()]
StatusQuery = Annotated[str | None, Query()]


def parse_status_filter(raw: str | None) -> list[str] | None:
    """Split `raw` on ',' into unchecked segments; None when absent or empty.
    No whitelist check here — that's the use case's gate."""
    if not raw:
        return None
    return raw.split(",")
