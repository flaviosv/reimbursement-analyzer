"""Per-bucket payload builders for tests/e2e's scenario tests (Phase 3),
layered on shared.testing.valid_reimbursement_item — each overrides only
the raw_ocr_text/claimed_amount_brl/submitted_at fields needed to steer a
real Groq extraction into its target bucket. Values are deliberately
round numbers and explicit far-past/near dates (design.md's
live-model-steering mitigation), not guaranteed outcomes: a live model can
still land elsewhere, which is a real signal per the suite's own chosen
strictness, not something this module masks."""

from datetime import UTC, datetime, timedelta

from shared.testing import valid_reimbursement_item

# apply_policies.py's REJECT_THRESHOLD_DAYS is 90 — comfortably clearing it
# without relying on a value near the boundary.
_REJECT_DAYS_PAST = 120
_APPROVE_VALUE_BRL = 85.00
# Both sides of the mismatch land inside apply_policies.py's ambiguous zone
# (200 < value <= 2000) regardless of which one a real extraction resolves
# to, so the bucket is reached either way.
_AMBIGUOUS_CLAIMED_AMOUNT_BRL = 1000.00
_AMBIGUOUS_OCR_TOTAL_BRL = 700.00


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _br_date(when: datetime) -> str:
    return when.strftime("%d/%m/%Y")


def approve_bucket_payload(request_id: str) -> dict:
    """Fresh, low-value (<=200 BRL), unambiguous receipt — targets
    apply_policies' auto-approve rule, which decides without ever
    reaching the analysis guardrail."""
    now = datetime.now(UTC)
    return valid_reimbursement_item(
        request_id,
        submitted_at=_iso_now(),
        raw_ocr_text=(
            "CAFE CENTRAL LTD\n"
            "TAX ID 11.222.333/0001-44\n"
            f"DATE {_br_date(now)}\n"
            "BUSINESS BREAKFAST\n"
            f"TOTAL R$ {_APPROVE_VALUE_BRL:.2f}"
        ),
        claimed_amount_brl=_APPROVE_VALUE_BRL,
    )


def reject_bucket_payload(request_id: str) -> dict:
    """Receipt dated well over 90 days before submitted_at — targets
    apply_policies' reject rule, which is checked before any value
    threshold."""
    now = datetime.now(UTC)
    receipt_date = now - timedelta(days=_REJECT_DAYS_PAST)
    return valid_reimbursement_item(
        request_id,
        submitted_at=_iso_now(),
        raw_ocr_text=(
            "URBAN TAXI SERVICES\n"
            "TAX ID 22.333.444/0001-55\n"
            f"DATE {_br_date(receipt_date)}\n"
            "ORIGIN: COMPANY HQ\n"
            "DESTINATION: AIRPORT TERMINAL 3\n"
            f"FARE R$ {_APPROVE_VALUE_BRL:.2f}"
        ),
        claimed_amount_brl=_APPROVE_VALUE_BRL,
    )


def human_review_bucket_payload(request_id: str) -> dict:
    """Value inside the 200-2000 BRL ambiguous zone, with a deliberate
    claimed-amount/OCR-total mismatch for the guardrail's consistency
    check to catch."""
    now = datetime.now(UTC)
    return valid_reimbursement_item(
        request_id,
        submitted_at=_iso_now(),
        raw_ocr_text=(
            "GRAND HOTEL LTD\n"
            "TAX ID 33.444.555/0001-66\n"
            f"DATE {_br_date(now)}\n"
            "TWO NIGHT STAY\n"
            f"TOTAL R$ {_AMBIGUOUS_OCR_TOTAL_BRL:.2f}"
        ),
        claimed_amount_brl=_AMBIGUOUS_CLAIMED_AMOUNT_BRL,
    )
