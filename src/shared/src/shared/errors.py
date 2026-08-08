from pydantic import ValidationError


class PayloadTooLarge(Exception):
    """The request body exceeded MAX_BODY_BYTES while streaming."""


class BatchInvalid(Exception):
    """The batch failed schema validation. The message names the offending
    item index and field only — never the value."""


class PublishFailed(Exception):
    """The broker returned a delivery error, or no delivery report arrived
    within the publish timeout."""


class ReimbursementFilterInvalid(Exception):
    """The requested status/limit/offset combination failed the list use
    case's gate: an out-of-whitelist status value, or an out-of-bounds
    limit/offset."""


class ReviewInvalid(Exception):
    """The review payload failed its own shape contract (missing/malformed
    field, or an unrecognized status value)."""


class ReimbursementNotFound(Exception):
    """No reimbursement row matches the given uuid."""


class ReimbursementNotEligible(Exception):
    """The row exists but its current state doesn't allow this decision —
    wrong status, or (reject only) missing receipts_value/date/currency."""


def sanitize(exc: BaseException) -> str:
    """Render an exception for stdout: its type, plus whatever diagnostic
    shape is safe to include for that exception kind — never a raw value.

    This covers stdout only. `str(exc)` still reaches `AttemptError.message`
    unsanitized in `_requeue` — deliberately, per PUB-15/the envelope's own
    trust-boundary framing, with the newline-injection angle covered
    separately (see `render_history`'s `_one_line`). The reason stdout gets
    this treatment and the wire/DB path does not isn't that stdout is
    uniquely risky — it's that `str(exc)` is simply never called here,
    which is what keeps a Postgres DETAIL line's quoted column values
    (e.g. a unique violation embedding the submitter's email) from
    reaching it at all.
    """
    if isinstance(exc, ValidationError):
        # type(exc).__name__ alone ("ValidationError") was zero diagnostic
        # content past whether coercion failed at all — field locations
        # are safe (they're schema paths, not user data) and tell a
        # reader what actually failed.
        locations = ", ".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        return f"{type(exc).__name__}: {locations}" if locations else type(exc).__name__
    constraint_name = getattr(exc, "constraint_name", None)
    if constraint_name:
        return f"{type(exc).__name__}: {constraint_name}"
    return type(exc).__name__
