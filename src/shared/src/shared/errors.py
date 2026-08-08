class PayloadTooLarge(Exception):
    """The request body exceeded MAX_BODY_BYTES while streaming."""


class BatchInvalid(Exception):
    """The batch failed schema validation. The message names the offending
    item index and field only — never the value."""


class PublishFailed(Exception):
    """The broker returned a delivery error, or no delivery report arrived
    within the publish timeout."""


class DuplicateRequest(Exception):
    """The item collided with an already-stored request on the
    (request_id, lower(submitted_by)) unique index. Retrying can never fix
    it, so it is dropped rather than retried."""


def sanitize(exc: BaseException) -> str:
    """Render an exception for stdout: its type, plus the violated
    constraint when there is one.

    Nothing else, deliberately. Postgres quotes the offending column values
    in its DETAIL line — a unique violation on this schema embeds the
    submitter's email — and stdout sits outside the payload's trust
    boundary, unlike the envelope, the row, and the failure log (PUB-15).
    """
    constraint_name = getattr(exc, "constraint_name", None)
    if constraint_name:
        return f"{type(exc).__name__}: {constraint_name}"
    return type(exc).__name__
