from shared.errors import sanitize


class _DriverError(Exception):
    """The attribute surface asyncpg's PostgresError exposes — the message,
    a `constraint_name`, and the value-bearing `detail` line."""

    def __init__(self, message: str, *, constraint_name: str, detail: str) -> None:
        super().__init__(message)
        self.constraint_name = constraint_name
        self.detail = detail


class DescribeSanitize:
    def it_renders_only_the_exception_type_when_no_constraint_is_named(self) -> None:
        assert sanitize(RuntimeError("connection reset by peer")) == "RuntimeError"

    def it_renders_the_exception_type_and_the_constraint_name(self) -> None:
        exc = _DriverError(
            'duplicate key value violates unique constraint "reimbursement_request_submitter_key"',
            constraint_name="reimbursement_request_submitter_key",
            detail="Key (request_id, lower(submitted_by))=(REQ-1, ana@company.com) already exists",
        )

        assert sanitize(exc) == "_DriverError: reimbursement_request_submitter_key"

    def it_never_leaks_the_submitter_email_the_driver_quotes_in_detail(self) -> None:
        exc = _DriverError(
            'duplicate key value violates unique constraint "reimbursement_request_submitter_key"',
            constraint_name="reimbursement_request_submitter_key",
            detail="Key (request_id, lower(submitted_by))=(REQ-1, ana@company.com) already exists",
        )

        rendered = sanitize(exc)

        assert "ana@company.com" not in rendered
        assert "REQ-1" not in rendered
        assert "already exists" not in rendered

    def it_never_raises_on_an_exception_carrying_neither_attribute(self) -> None:
        class _Bare(Exception):
            pass

        rendered = sanitize(_Bare())

        assert rendered == "_Bare"
