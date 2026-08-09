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
- Make human-review cases available for review, with the reviewer's decision recorded — **implemented**: `GET /api/v1/reimbursement` lists cases (filterable by status, paginated), `PUT /api/v1/reimbursement/{uuid}` records a reviewer's approve/reject decision.
- Maintain an audit trail for every decision.

**Out of scope:** not explicitly documented beyond the above; this is a scoped technical test, not a production roadmap. `docs/SCOPE.md`'s Phase 2 backlog additionally lists load testing, E2E testing, and authentication as future work — not current scope.

## Status

Early-stage, partially implemented:

- **`api`** — the full reimbursement HTTP surface is implemented and tested: `POST /api/v1/reimbursement` (intake — request validation, a 1 MiB body ceiling, publish to Kafka), `GET /api/v1/reimbursement` (list, with status/limit/offset filtering), and `PUT /api/v1/reimbursement/{uuid}` (records a human reviewer's approve/reject decision). **PUT is a human-driven decision-recording endpoint, not the automated decision layer** — a reviewer calls it after making up their own mind on a row already at `human-review`/`auto-rejected`/`human-rejected`; it does not itself evaluate or classify anything. The automated decision logic (rules engine, auto-approve/reject/human-review classification) described in Vision & Goals is **not yet implemented anywhere in this codebase**.
- **`publisher`** — implemented: consumes `Request`, inserts one `reimbursement` row per item, and publishes one `Reimbursement` message per item, with retry-with-history on transient failures, duplicate detection, and `retry > 3` escalation to a `human-review` status. Fully tested, including a real Kafka+Postgres integration round trip.
- **`reimbursement`** (the package formerly named `agent`, renamed and flattened to match `publisher`/`api`'s layout) — its consume/resolve layer is implemented: consumes `Reimbursement`, resolves the row by `uuid`, tolerates a stale or ghost message, requeues transient failures, and escalates past its own retry ceiling to `human-review` (reusing the same escalation action `publisher` uses). Fully tested, including a real Kafka+Postgres integration round trip. The decision layer itself — the rules/LLM evaluation that actually classifies a request as auto-approved, auto-rejected, or human-review, described in Vision & Goals above — has a scaffold (a LangGraph `StateGraph` with five stub nodes, under the package's nested `agent/` subdirectory) but **no working implementation, and it is not called from the resolve path yet**. This remains the single biggest gap in the project; its design is in progress (`.specs/features/agent-decide-reimbursement/`).
- Database schema exists for `reimbursement` and `human_review`. `reimbursement` is actively written by `publisher` (`pending` rows on success, `human-review` rows past its retry ceiling), the `reimbursement` package (`human-review` rows past its own retry ceiling), and now `api` (`human-approved`/`human-rejected` rows via `PUT`). `human_review` is now actively written too — `api`'s `PUT` endpoint records one row per reviewer decision via `review_reimbursement`, appended in the same transaction as the `reimbursement` row's status update.
