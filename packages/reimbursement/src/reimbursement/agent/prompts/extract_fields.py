"""Extraction prompt — resolves `value`, `currency`, `receipts_date` from the
allow-listed payload fields (AGD-01..04, AGD-26)."""

from langchain_core.prompts import ChatPromptTemplate

PLACEHOLDER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You extract three fields from a Brazilian expense-reimbursement "
            "claim: `value` (the requested reimbursement amount), `currency` "
            "(always BRL), and `receipts_date` (the receipt/transaction date, "
            "as an ISO-8601 date).\n\n"
            "The payload may carry a `claimed_amount_brl` field already — "
            "treat it as one signal among others, not a shortcut: still read "
            "`raw_ocr_text` and reconcile it against `claimed_amount_brl` and "
            "`claimed_category` before deciding on `value`.\n\n"
            "For `receipts_date`, `raw_ocr_text` may contain more than one "
            "date (e.g. a hotel folio's check-in and check-out dates). Pick "
            "the date the expense was actually incurred or paid, not an "
            "unrelated stay/booking date.\n\n"
            "If you cannot confidently resolve a field from the payload, "
            "return null for it rather than guessing — a missing field "
            "routes this claim to human review instead of a wrong "
            "auto-decision.",
        ),
        ("human", "{payload}"),
    ]
)
