"""Placeholder guardrail/consistency-check prompt — no real prompt
engineering this session, deferred to a follow-up. Only the input variable
this node reads (`extracted`) is fixed."""

from langchain_core.prompts import ChatPromptTemplate

PLACEHOLDER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "TODO: assess whether the resolved fields below are internally "
            "consistent enough to auto-approve.",
        ),
        ("human", "{extracted}"),
    ]
)
