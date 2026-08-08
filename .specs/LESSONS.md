# LESSONS — auto-maintained by scripts/lessons.py

> Machine-owned. Do NOT hand-edit. Changes are overwritten on the next `lessons.py` write.
> Canonical state lives in `.specs/lessons.json`. Edit lessons only via the script.
> promote_threshold=2 distinct features · window_days=45 · quarantine_threshold=2

## Confirmed (load these at Specify/Design)

Corroborated across multiple features. Safe to apply as guidance.

_none_

## Candidates (under observation — do NOT load as guidance yet)

Seen once or not yet corroborated. Tracked, not trusted.

### L-001 — When an acceptance criterion lists multiple failure sub-cases for one field (e.g. not-parseable vs. parseable-but-missing-X), write a distinct test per sub-case rather than one representative test per field.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `validation` · harmful: 0
- features: api-post-reimbursement
- evidence: RCV-09 / test_validation.py:108-117 (validation)
- last seen: 2026-08-07T23:06:16Z

### L-002 — Before writing an acceptance criterion that names two distinct failure triggers (e.g. broker-unreachable vs. delivery-timeout), confirm the planned implementation actually has two distinct code paths for them, or phrase it as one AC to avoid an unfalsifiable precision gap at validation time.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `spec-writing` · harmful: 0
- features: api-post-reimbursement
- evidence: spec.md P1 'Publish failure is never silently accepted' AC4 (spec-writing)
- last seen: 2026-08-07T23:06:24Z

### L-003 — A spec acceptance criterion claiming a memory or performance property should also state the observable proxy to test it by, or validation can only prove it by code inspection rather than by assertion.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `spec-writing` · harmful: 0
- features: api-post-reimbursement
- evidence: spec.md P1 '25 MB ceiling enforced end to end' AC2 / test_validation.py:47-52 (spec-writing)
- last seen: 2026-08-07T23:06:24Z

### L-004 — testcontainers' KafkaContainer.with_kraft() enforces Confluent Platform's own version numbering and rejects tags below 7.0.0, so it cannot be pinned to a non-Confluent broker image (e.g. apache/kafka) regardless of that image's real Kafka version -- verify testcontainers image compatibility empirically before assuming a project's real broker image can be reused in an integration test.
- signal: `spec_deviation` · recurrence: 1 feature(s) · scope: `testing,kafka` · harmful: 0
- features: api-post-reimbursement
- evidence: src/api/tests/reimbursement/create/conftest.py:7 (testing,kafka)
- last seen: 2026-08-07T23:06:30Z

## Quarantined (failed when applied — ignore)

A confirmed lesson that recurred alongside failure. Kept for the maintainer to review.

_none_
