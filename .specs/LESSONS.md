# LESSONS — auto-maintained by scripts/lessons.py

> Machine-owned. Do NOT hand-edit. Changes are overwritten on the next `lessons.py` write.
> Canonical state lives in `.specs/lessons.json`. Edit lessons only via the script.
> promote_threshold=2 distinct features · window_days=45 · quarantine_threshold=2

## Confirmed (load these at Specify/Design)

Corroborated across multiple features. Safe to apply as guidance.

### L-003 — A spec acceptance criterion claiming a memory or performance property should also state the observable proxy to test it by, or validation can only prove it by code inspection rather than by assertion.
- signal: `spec_precision_gap` · recurrence: 2 feature(s) · scope: `spec-writing` · harmful: 0
- features: api-post-reimbursement, publisher-consume-request
- evidence: spec.md P1 '25 MB ceiling enforced end to end' AC2 / test_validation.py:47-52 (spec-writing) (+1 more)
- last seen: 2026-08-08T17:20:19Z

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

### L-004 — testcontainers' KafkaContainer.with_kraft() enforces Confluent Platform's own version numbering and rejects tags below 7.0.0, so it cannot be pinned to a non-Confluent broker image (e.g. apache/kafka) regardless of that image's real Kafka version -- verify testcontainers image compatibility empirically before assuming a project's real broker image can be reused in an integration test.
- signal: `spec_deviation` · recurrence: 1 feature(s) · scope: `testing,kafka` · harmful: 0
- features: api-post-reimbursement
- evidence: src/api/tests/reimbursement/create/conftest.py:7 (testing,kafka)
- last seen: 2026-08-07T23:06:30Z

### L-005 — When an acceptance criterion requires a log record to carry a specific identifier, assert that identifier is present, not only that forbidden content is absent.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `logging` · harmful: 0
- features: publisher-consume-request
- evidence: PUB-15 / src/publisher/src/processing.py:216 (logging)
- last seen: 2026-08-08T17:20:18Z

### L-006 — When the design deliberately inverts an acceptance criterion, amend that criterion in the spec in the same change, or spec and code disagree in writing at validation time.
- signal: `spec_deviation` · recurrence: 1 feature(s) · scope: `spec-writing` · harmful: 0
- features: publisher-consume-request
- evidence: PUB-33 / design.md:388,448 vs spec.md P1 bad-input AC5 (spec-writing)
- last seen: 2026-08-08T17:20:18Z

### L-007 — Process-lifecycle wiring such as signal handlers and startup hooks needs a test asserting it was armed, because no functional test exercises it.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: publisher-consume-request
- evidence: mutant M9 / src/publisher/src/consumer.py:87-90 (testing)
- last seen: 2026-08-08T17:20:18Z

### L-008 — Test both sides of every numeric threshold an acceptance criterion names, the last passing value and the first failing one, or an off-by-one in the comparison survives.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: publisher-consume-request
- evidence: mutant M10 / src/publisher/src/processing.py:90 (testing)
- last seen: 2026-08-08T17:20:18Z

### L-009 — An integration test claiming a configuration value is load-bearing must be shown to fail when that value is reverted to its default, or it proves only that the scenario works.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: publisher-consume-request
- evidence: mutant M8 / src/publisher/tests/test_integration.py:211-232 (testing)
- last seen: 2026-08-08T17:20:19Z

### L-010 — An acceptance criterion phrased as a negative universal such as no behaviour shall depend on X gives no test to write, so restate it as a concrete observation that can be asserted.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `spec-writing` · harmful: 0
- features: publisher-consume-request
- evidence: PUB-39 / spec.md P1 concurrency AC4 (spec-writing)
- last seen: 2026-08-08T17:20:19Z

### L-011 — Add a test at the exact boundary value of every numeric ceiling comparison (e.g. retry == MAX_RETRY), not just values comfortably past it, so a > -> >= mutation is caught
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: agent-consume-reimbursement
- evidence: validation.md — Discrimination Sensor mutation 2, agent/validation.py:81 (testing)
- last seen: 2026-08-08T19:19:01Z

### L-012 — When an acceptance criterion requires both a positive outcome and a negative side-effect claim (e.g. no republish, no failure-log entry), assert both explicitly, not just the positive outcome
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: agent-consume-reimbursement
- evidence: validation.md — AGT-03, AGT-17 (agent/tests/test_validation.py:145-160,74-92) (testing)
- last seen: 2026-08-08T19:19:01Z

## Quarantined (failed when applied — ignore)

A confirmed lesson that recurred alongside failure. Kept for the maintainer to review.

_none_
