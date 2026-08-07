# Project

## Overview

Event-driven service that decides the outcome of expense reimbursement
requests — auto-approve, auto-reject with justification, or route to human
review. Built as a technical test for ReimbursementAnalyzer by Flavio Studart.

## Vision & goals

- Decide every submitted request using deterministic business rules first,
  with a probabilistic (LLM/SLM) layer only when the payload is not
  machine-readable.
- Guarantee full traceability and auditability of every decision, automated
  or human. The service is mission-critical in a financial domain where wrong
  decisions cause financial loss, policy violations, or audit failure.
- Never lose a request: ingestion publishes to Kafka before any processing,
  and repeated failures escalate to human review instead of being discarded.

## Target users

- Auditors tracing how each decision was produced.
- Employees submitting reimbursement requests through the HTTP API.
- Human reviewers approving or rejecting escalated requests.

## Scope

**In scope** (per `docs/SCOPE.md`):

- Agent service: deterministic and probabilistic decision layers.
- Audit trail: LangFuse tracing with file-logging fallback.
- Publisher service: persist requests, re-publish for downstream processing.
- REST API: `POST`/`GET` `/api/v1/reimbursement`,
  `PUT /api/v1/reimbursement/:uuid`.

**Out of scope** (deliberate decisions recorded in `docs/SCOPE.md`):

- Attachment validation against `raw_ocr_text`.
- Authentication (focus kept on business rules; reviewer identity is a plain
  email field for now).
- Data extraction / ETL from payloads.
- Phase 2 items: human-review evaluator, event triggers, deterministic-layer
  tuning.

## Status

Bootstrap phase. The uv monorepo, Docker Compose stack, and service skeletons
are merged, but every service is still a structural placeholder (health
endpoint plus sample Kafka consumers). `docs/SCOPE.md` is the authoritative
target design; `docs/SCOPE_GAP_ANALYSIS.md` tracks open gaps against the
original requirements PDF (`docs/original/Requirements.pdf`).
