from pydantic import BaseModel, ValidationError

from shared.errors import sanitize


class _Model(BaseModel):
    request_id: str
    submitted_by: str


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

    def it_names_the_failed_fields_of_a_validation_error(self) -> None:
        # type(exc).__name__ alone was zero diagnostic content past "some
        # field failed" (A10) — field locations are schema paths, not user
        # data, so they're safe to include.
        try:
            _Model.model_validate({"request_id": 1, "submitted_by": 2})
        except ValidationError as exc:
            rendered = sanitize(exc)

        assert "request_id" in rendered
        assert "submitted_by" in rendered

    def it_never_leaks_the_offending_value_of_a_validation_error(self) -> None:
        try:
            _Model.model_validate({"request_id": "REQ-1", "submitted_by": ["ana@company.com"]})
        except ValidationError as exc:
            rendered = sanitize(exc)
        else:
            raise AssertionError("expected a ValidationError")

        assert "ana@company.com" not in rendered
        assert "submitted_by" in rendered
