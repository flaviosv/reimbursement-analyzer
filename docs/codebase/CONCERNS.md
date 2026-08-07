# Codebase concerns

**Analysis date:** 2026-08-07

## Missing critical features

**All business logic is placeholder:**

- Problem: the API exposes only `/health`; both consumers subscribe to
  placeholder topics (`sample-topic`, `sample-queue`) and print messages.
  None of the endpoints, decision layers, or persistence in `docs/SCOPE.md`
  exists.
- Files: `src/api/src/api/main.py`, `src/agent/src/agent/consumer.py`,
  `src/publisher/src/publisher/consumer.py`
- Blocks: every functional requirement.
- Note: expected at this phase — listed so no reader mistakes the wiring for
  a working system.

**No database schema or migrations:**

- Problem: `docs/SCOPE.md` mandates migration-managed schemas for
  `Reimbursement` and `HumanReview`; no migration tool is installed and no
  SQL exists. Bootstrap checks (topics created, DB structure ensured) are
  also unimplemented.
- Files: none (absence); `docker-compose.yml` only provisions the empty
  `reimbursementanalyzer` database.
- Fix approach: pick a migration tool compatible with asyncpg-based access
  and wire it into service startup or a dedicated bootstrap step.

**Kafka cannot carry the required 25 MB payloads:**

- Problem: the API contract accepts payloads up to 25 MB and publishes them
  whole to Kafka, but the broker in `docker-compose.yml` sets no
  `message.max.bytes` (default is ~1 MB). Producer/consumer limits will also
  need raising.
- Files: `docker-compose.yml` (kafka service environment)
- Impact: any payload over ~1 MB fails at publish, which per the contract
  rejects the request.
- Fix approach: raise broker `message.max.bytes` (and matching
  producer/consumer settings) to >25 MB, or revisit the
  payload-through-Kafka decision for large messages.

## Test coverage gaps

**Zero tests in a mission-critical financial service:**

- What's not tested: everything (see `TESTING.md`).
- Risk: decision logic (thresholds 200 / 2000 / 90 days, retry semantics,
  transactional publish) is exactly the kind of code that silently regresses.
- Priority: high — before or alongside the first real endpoint.

## Tech debt

**Print-based logging:**

- Issue: consumers use `print()`; no levels, no structure, no correlation
  ids — inadequate for the audit-trail requirement.
- Files: `src/agent/src/agent/consumer.py`,
  `src/publisher/src/publisher/consumer.py`
- Fix approach: adopt structured stdlib `logging` (JSON) before the real
  consumers land.

**No lint/format/type-check tooling:**

- Issue: `.dockerignore` anticipates ruff/mypy caches, but neither tool is
  configured, so no gate exists for style or typing.
- Files: none (absence)
- Fix approach: add ruff (lint + format) and a type checker to the workspace
  dev dependencies.

## Fragile areas

**`docs/SCOPE.md` internal inconsistencies:**

- Files: `docs/SCOPE.md`
- Why fragile: the scope document is the implementation contract, and it
  contains contradictions (duplicate/missing enum values, precedence between
  the 90-day reject rule and the >2000 review gate, fields referenced but
  absent from the schema).
- Safe-modification notes: `docs/SCOPE_GAP_ANALYSIS.md` enumerates all 15+
  findings — resolve the relevant one there before implementing the affected
  behavior.

## Security considerations

**No authentication on the API:**

- Risk: anyone who can reach the API can submit or (once implemented)
  approve reimbursements. Reviewer identity is a caller-supplied email.
- Files: `src/api/src/api/main.py`
- Current mitigation: deliberate, documented scope decision
  (`docs/SCOPE.md`); local-only deployment.
- Recommendation: before any shared deployment, add authentication and derive
  reviewer identity from the credential (the scope itself suggests JWT).

**Dev-only placeholder credentials:**

- Risk: `.env.sample` values are weak and guessable by design.
- Files: `.env.sample`, `docker-compose.yml`
- Current mitigation: clearly documented as local-only; `.env` is gitignored;
  most ports bind to `127.0.0.1` (exceptions: api 8000, langfuse-web 3000,
  minio 9090 — the last is intentional for browser media access).

## Scaling limits

**Single-node Kafka, replication factor 1:**

- Current capacity: one KRaft broker, all replication/ISR settings at 1.
- Limit: no durability or availability beyond the single node — acceptable
  for local dev only.
- Files: `docker-compose.yml`
- Scaling path: multi-broker cluster with RF >= 3 in any real deployment.
