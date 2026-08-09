"""Guardrail/consistency-check prompt — the ambiguous-zone judge (AGD-17..20).
Only sees the resolved `extracted` fields (value/currency/receipts_date), not
the original payload — the specific checks it runs are deliberately left
open by spec.md; this prompt exercises plausibility on what it can see."""

from langchain_core.prompts import ChatPromptTemplate

PLACEHOLDER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are the second-opinion guardrail for a Brazilian "
            "expense-reimbursement claim whose requested value fell between "
            "R$200 and R$2000 — too small to require mandatory human "
            "review, too large to auto-approve outright.\n\n"
            "You will see the fields an earlier extraction step resolved: "
            "`value`, `currency`, `receipts_date`. Judge whether they form "
            "an internally consistent, plausible reimbursement record: the "
            "currency should be BRL, the value should be a sane amount for "
            "this range (not zero, not implausibly precise or round in a "
            "way that suggests a guess), and the date should be a real "
            "calendar date that isn't in the future relative to today.\n\n"
            "Set `consistent` to true only if nothing about these fields "
            "looks contradictory or fabricated; set it to false if anything "
            "looks off. Always give a concrete `reasoning` naming what you "
            "checked — it becomes the durable, auditable reason for this "
            "claim's final decision, so a generic answer is not acceptable.",
        ),
        ("human", "{extracted}"),
    ]
)
