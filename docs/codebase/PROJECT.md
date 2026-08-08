# Project

## Overview

ReimbursementAnalyzer Reimbursement Decisioning Service — a mission-critical, event-driven system that decides the outcome of expense reimbursement requests in a financial domain. For each submitted request it produces one of three decisions: auto-approve, auto-reject (with justification), or route to human review, combining deterministic business rules with a probabilistic (LLM/SLM) evaluation layer.

> This project is a technical test for ReimbursementAnalyzer.

## Vision & Goals

- Decide reimbursement outcomes correctly and consistently under a documented approval policy (`docs/SCOPE.md`).
- Because money decisions are partly driven by probabilistic LLM output, guarantee full traceability and auditability of every decision — automated or human — as a hard requirement, not an afterthought.
- Keep the system resilient to partial failure: the database schema enforces its own invariants (constraints, an append-only audit trigger) rather than trusting application code alone.

## Target Users

- **Requesters** — submit reimbursement requests via the public HTTP API.
- **Human reviewers** — resolve requests the automated policy routes to human review.
- **Auditors** — rely on the recorded decision trail (automated and human) for compliance review.

## Scope

**In scope:**
- Accept reimbursement requests and produce a decision for each (`POST /api/v1/reimbursement`).
- Apply a defined set of business rules to each request.
- Classify every decision as auto-approved, human-review, or auto-rejected.
- Make human-review cases available for review, with the reviewer's decision recorded.
- Maintain an audit trail for every decision.

**Out of scope:** not explicitly documented beyond the above; this is a scoped technical test, not a production roadmap.

## Status

Early-stage, partially implemented:

- **`api`** — the POST-reimbursement intake endpoint is fully implemented: request validation, a 1 MiB body ceiling, and publishing to Kafka. The decision logic itself (rules engine, auto-approve/reject/human-review classification) is **not yet implemented anywhere in this codebase**.
- **`publisher`** — implemented: consumes `Request`, inserts one `reimbursement` row per item, and publishes one `Reimbursement` message per item, with retry-with-history on transient failures, duplicate detection, and `retry > 3` escalation to a `human-review` status. Fully tested, including a real Kafka+Postgres integration round trip.
- **`agent`** — its consume/resolve layer is implemented: consumes `Reimbursement`, resolves the row by `uuid`, tolerates a stale or ghost message, requeues transient failures, and escalates past its own retry ceiling to `human-review` (reusing the same escalation action `publisher` uses). Fully tested, including a real Kafka+Postgres integration round trip. The decision layer itself — the rules/LLM evaluation that actually classifies a request as auto-approved, auto-rejected, or human-review, described in Vision & Goals above — is **not yet implemented anywhere in this codebase**.
- Database schema exists for `reimbursement` and `human_review`. `reimbursement` is now actively written by both `publisher` (`pending` rows on success, `human-review` rows past its retry ceiling) and `agent` (`human-review` rows past its own retry ceiling). `human_review` remains unwritten by either — no code yet records an actual review event there.
