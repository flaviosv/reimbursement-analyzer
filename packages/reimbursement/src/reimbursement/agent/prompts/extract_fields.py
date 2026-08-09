"""Placeholder extraction prompt — no real prompt engineering this session,
deferred to a follow-up. Only the input variable this node reads
(`payload`) is fixed."""

from langchain_core.prompts import ChatPromptTemplate

PLACEHOLDER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "TODO: resolve the requested value, currency, and receipts_date "
            "from the reimbursement payload below.",
        ),
        ("human", "{payload}"),
    ]
)
