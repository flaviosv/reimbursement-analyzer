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

### L-011 — When a use case wraps multiple writes in one DB transaction for atomicity, add a test that injects a failure between the writes and asserts both are rolled back — happy-path and reject-all-writes tests alone don't prove the transaction boundary does anything
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `repo-layer` · harmful: 0
- features: api-put-reimbursement
- evidence: validation.md#Discrimination Sensor mutation 4 (repo-layer)
- last seen: 2026-08-08T21:02:55Z

### L-012 — When a spec AC's THEN clause has multiple parts (status code, error message content, no DB change), assert every part, not just the status code — a passing status-code assertion can hide an unenforced message or DB-state clause
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `api-error-contract` · harmful: 0
- features: api-put-reimbursement
- evidence: validation.md#REVIEW-02,REVIEW-03,REVIEW-06,REVIEW-08 (api-error-contract)
- last seen: 2026-08-08T21:03:00Z

### L-013 — When a query result is filtered on a column an index also sorts by, prove ORDER BY is load-bearing with a test whose data/filter can't be coincidentally satisfied by that index's own scan order.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `repository-layer` · harmful: 0
- features: api-get-reimbursement
- evidence: src/shared/src/shared/reimbursement/repository.py:52 (mutant #3, ORDER BY removed) (repository-layer)
- last seen: 2026-08-08T21:05:36Z

### L-014 — When a spec AC lists multiple invalid-input variants (e.g. out-of-range vs. non-integer), write a discriminating test for each variant, not just the ones that need new code.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `validation` · harmful: 0
- features: api-get-reimbursement
- evidence: LIST-02 (validation)
- last seen: 2026-08-08T21:05:42Z

### L-015 — When a spec's own Independent Test names a specific input combination, write that exact test rather than substituting a simpler case that exercises the same code path but not the same scenario.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `validation` · harmful: 0
- features: api-get-reimbursement
- evidence: LIST-07 (validation)
- last seen: 2026-08-08T21:05:50Z

### L-016 — When a spec AC requires an error to be logged, assert the log via caplog, not just the response status/body.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `error-handling` · harmful: 0
- features: api-get-reimbursement
- evidence: LIST-11 (error-handling)
- last seen: 2026-08-08T21:05:54Z

### L-017 — When a spec AC is a conjunction of conditions, write one test asserting the full conjunction rather than proving each condition in a separate test that individually omits the others.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `test-design` · harmful: 0
- features: api-get-reimbursement
- evidence: LIST-01 (test-design)
- last seen: 2026-08-08T21:05:59Z

### L-018 — Add a test at the exact boundary value of every numeric ceiling comparison (e.g. retry == MAX_RETRY), not just values comfortably past it, so a > -> >= mutation is caught
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: agent-consume-reimbursement
- evidence: validation.md — Discrimination Sensor mutation 2, agent/validation.py:81 (testing)
- last seen: 2026-08-08T19:19:01Z

### L-019 — When an acceptance criterion requires both a positive outcome and a negative side-effect claim (e.g. no republish, no failure-log entry), assert both explicitly, not just the positive outcome
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: agent-consume-reimbursement
- evidence: validation.md — AGT-03, AGT-17 (agent/tests/test_validation.py:145-160,74-92) (testing)
- last seen: 2026-08-08T19:19:01Z

### L-020 — When design.md assumes a tracing/observability dependency that a task list never adds, land the dependency (or formally re-scope the AC) before Tasks closes — a documented SPEC_DEVIATION comment is not a substitute for the AC's own test coverage.
- signal: `spec_deviation` · recurrence: 1 feature(s) · scope: `tracing` · harmful: 0
- features: agent-decide-reimbursement
- evidence: packages/reimbursement/src/reimbursement/agent/agent.py:92-101 SPEC_DEVIATION (tracing)
- last seen: 2026-08-09T19:13:04Z

### L-021 — Cross-check every spec.md requirement ID against design.md and tasks.md before Tasks starts — a requirement absent from both silently drops out of implementation scope with no error anywhere in the pipeline.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `spec-design-handoff` · harmful: 0
- features: agent-decide-reimbursement
- evidence: AGD-26 (spec-design-handoff)
- last seen: 2026-08-09T19:13:04Z

### L-022 — When a spec requires a message/reason field to state specific content, assert a substring of the expected content, not that the captured value equals itself (fake.calls == [(..., result[field])] passes for any content).
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `test-quality` · harmful: 0
- features: agent-decide-reimbursement
- evidence: AGD-13, AGD-16 (packages/reimbursement/tests/test_apply_policies.py) (test-quality)
- last seen: 2026-08-09T19:13:04Z

### L-023 — When a spec requires stamping an attribute at every hop where a value becomes known, check every branch of that hop's handler including escalation/error branches, not just the primary success path — a use-case return value that is discarded is a common place this slips.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `tracing` · harmful: 0
- features: RA-2-adding-tracing-support
- evidence: P2 AC2 / packages/publisher/src/publisher/processing.py:214-254 (tracing)
- last seen: 2026-09-06T17:09:17Z

### L-024 — When a process-wide tracer/provider singleton blocks running multiple real service entrypoints together in one test process, a hand-simulated multi-hop test proves only the propagation mechanism, not the full real-service lifecycle — treat the full-lifecycle acceptance criterion as still open until proven against real entrypoints or live infra.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `tracing` · harmful: 0
- features: RA-2-adding-tracing-support
- evidence: P1 AC7 / packages/publisher/tests/test_trace_propagation_integration.py:1-17 (tracing)
- last seen: 2026-09-06T17:09:17Z

### L-025 — When a task's own Tests field promises a test mirroring an already-built sibling test, verify that test was actually added before marking the task done — a promised-but-missing unit test for a composition-root call site is easy to lose track of.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `testing` · harmful: 0
- features: RA-2-adding-tracing-support
- evidence: T9 Done-when / packages/publisher/src/publisher/consumer.py:126,145 (testing)
- last seen: 2026-09-06T17:09:17Z

## Quarantined (failed when applied — ignore)

A confirmed lesson that recurred alongside failure. Kept for the maintainer to review.

_none_
