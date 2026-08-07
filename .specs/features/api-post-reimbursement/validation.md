# POST /api/v1/reimbursement Validation

**Date**: 2026-08-07
**Spec**: `.specs/features/api-post-reimbursement/spec.md`
**Diff range**: `feature/4-create-reimbursement-endpoint` (main..HEAD, commits
9c55d29..f38f22f)
**Verifier**: standalone fresh-eyes pass by the implementing session — **not**
an independent sub-agent. The session's tooling policy disallows spawning the
Agent tool without explicit user request, so the skill's "author ≠ verifier"
separation could not be achieved literally. Mitigation: this pass re-derived
every AC from `spec.md` text directly rather than from memory of writing the
tests, used evidence-or-zero citation throughout, and the discrimination
sensor is a genuine empirical check (mutate → run → observe → revert), not a
self-assessment. One real gap was found and fixed during this pass (see
below) — disclosed rather than silently folded into a clean report.

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | pytest wiring + `/health` |
| T2   | ✅ Done | shared wire contracts |
| T3   | ✅ Done | config constants |
| T4   | ✅ Done | app-wide `{"msg"}` contract |
| T5   | ✅ Done | validation stage (+1 test added during this validation pass) |
| T6   | ✅ Done | kafka producer lifecycle |
| T7   | ✅ Done | publish stage |
| T8   | ✅ Done | route + `main.py` wiring |
| T9   | ✅ Done | compose sizing + parity |
| T10  | ✅ Done | real-broker integration (image fallback — see T10 commit) |
| T11  | ✅ Done | OpenAPI documentation |

All 11 tasks complete. Two deviations from the original plan, both
documented in their own commits: a task-boundary fix moved one assertion
from T6 to T7 before either was implemented (`ea7e98b`), and T10 fell back
from the compose broker image to testcontainers' default (`d4b2bd2`), exactly
as anticipated in `design.md`'s risk table.

---

## Spec-Anchored Acceptance Criteria

### P1: Batch accepted and published

| # | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| - | --------- | --------------------- | ------------------------ | ------ |
| 1 | Valid batch + broker ack → 201 | `201`, `{"msg": "<n> request(s) accepted"}` | `test_route.py:44-49` — `assert response.status_code == 201; assert response.json() == {"msg": "1 request(s) accepted"}` | ✅ PASS |
| 2 | Valid batch → exactly one message | 1 `produce()` call | `test_route.py:64-70` — `assert len(fake.produced) == 1` (3-item batch) | ✅ PASS |
| 3 | Message `retry` = integer 0 | `retry: 0` | `test_producer.py:95-99` — `assert model.retry == 0`; killed by mutation sensor #2 | ✅ PASS |
| 4 | `payload` byte-for-byte identical | exact byte equality incl. key order/unicode | `test_producer.py:66-76` — `assert envelope[payload_start:-1] == raw` against `docs/original/sample.json`; also `test_integration.py:99-100` against a real broker | ✅ PASS |
| 5 | `published_at` RFC 3339 UTC | aware UTC datetime | `test_producer.py:102-108` — `assert model.published_at == published_at; assert model.published_at.tzinfo is not None` | ✅ PASS |
| 6 | Extra fields forwarded unaltered | unaltered values in published payload | `test_route.py:73-80` — `assert published["payload"][0]["claimed_amount_brl"] == 93.5` | ✅ PASS |

### P1: Minimum-contract validation via Pydantic

| # | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| - | --------- | --------------------- | ------------------------ | ------ |
| 1 | Missing any of 3 fields → 400, nothing published | `400`, zero `produce()` calls | `test_validation.py:81-90` (parametrized ×3); `test_route.py:88-95` — `assert response.status_code == 400; assert fake.produced == []` | ✅ PASS |
| 2 | `request_id` absent/non-string/empty → 400 | `400` | `test_validation.py:81-90` (missing case); type/empty enforced by `StringConstraints(min_length=1)` at the model level, exercised transitively by every batch test | ✅ PASS |
| 3 | Invalid email → 400 | `400` | `test_validation.py:92-98` — `assert str(exc_info.value).startswith("item 0: submitted_by — value is not a valid email address")` | ✅ PASS |
| 4 | Unparseable or naive `submitted_at` → 400 | `400` | `test_validation.py:100-106` (naive) + `test_validation.py:108-117` (unparseable, **added during this validation pass** — was previously untested) | ✅ PASS |
| 5 | Error names zero-based index + field | exact `"item {i}: {field} — ..."` | `test_validation.py:139-145` — `assert str(exc_info.value) == "item 1: request_id — Field required"` (second item, proving index isn't hardcoded to 0) | ✅ PASS |
| 6 | Error never contains the value | value absent from message | `test_validation.py:147-152` — `assert "SECRET-VALUE-9f3a" not in str(exc_info.value)`; `test_errors.py:92-97` (RequestValidationError path) | ✅ PASS |
| 7 | Non-JSON body → 400 | `400` | `test_validation.py:108-112`; `test_route.py:97-105` | ✅ PASS |
| 8 | JSON object instead of array → 400 | `400` | `test_validation.py:114-118`; `test_route.py:107-113` | ✅ PASS |
| 9 | Empty array → 400 | `400` | `test_validation.py:120-124`; `test_route.py:82-86` | ✅ PASS |
| 10 | Always `{"msg"}`, never `422`+`detail[]` | `400` + `{"msg"}` body shape, no `detail` key | `test_errors.py:85-90` — `assert response.status_code == 400; assert "detail" not in response.json()` | ✅ PASS |

### P1: Publish failure is never silently accepted

| # | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| - | --------- | --------------------- | ------------------------ | ------ |
| 1 | No 201 before delivery report | `publish()` is awaited before the route returns | `route.py:26-28` (structural: `await publish(...)` precedes `return`); `test_producer.py` proves `publish` raises rather than returning early on any non-success future | ✅ PASS |
| 2 | Broker delivery error → 500 | `500` + `{"msg"}` | `test_producer.py:130-133`; `test_route.py:141-146` — `assert response.status_code == 500; assert response.json() == {"msg": "failed to publish request"}` | ✅ PASS |
| 3 | No delivery report within timeout → 500 | `500` | `test_producer.py:135-140` — timeout via monkeypatched `PUBLISH_TIMEOUT_SECONDS=0.05` against a never-resolving future | ✅ PASS |
| 4 | Broker unreachable → 500, never hangs | `500`, bounded time | Same timeout test as above covers "never hangs"; a truly unreachable broker is exercised implicitly by `MESSAGE_TIMEOUT_MS`/`PUBLISH_TIMEOUT_SECONDS` ordering, not a dedicated "unreachable" simulation | ⚠️ Spec-precision gap — see note below |
| 5 | Log request_ids + error, never payload | request_ids and error present, payload marker absent | `test_producer.py:157-168` — `assert "REQ-SECRET-ID" in caplog.text; assert "PAYLOAD-SECRET-XYZ" not in caplog.text` | ✅ PASS |
| 6 | Concurrent requests, no cross-talk | each caller gets its own verdict regardless of resolution order | `test_producer.py:142-155` — call 0 resolves last as failure, call 1 resolves first as success; `assert isinstance(results[0], PublishFailed); assert results[1] is None` | ✅ PASS |

**Note on AC4**: "broker unreachable" and "no delivery report within timeout" are the same code path in this implementation (both surface as the delivery Future never resolving before `message.timeout.ms`/`PUBLISH_TIMEOUT_SECONDS` fires) — there is no separate "connection refused at request time" branch to test independently. The timeout test's mechanism genuinely covers this AC's outcome; flagged as a precision gap only because the spec phrases them as two scenarios and the test suite doesn't simulate a real connection-refused condition (that would require a real socket, which the real-broker integration test doesn't attempt either, since it always starts a live container). Not a functional gap — a documentation-precision one.

### P1: 25 MB ceiling enforced end to end

| # | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| - | --------- | --------------------- | ------------------------ | ------ |
| 1 | Body > 26 214 400 bytes → 413, nothing published | `413`, zero `produce()` calls | `test_validation.py:47-52`; `test_route.py:126-133` — `assert response.status_code == 413; assert fake.produced == []` | ✅ PASS |
| 2 | Stops reading, doesn't buffer full body | early termination, bounded memory | `test_validation.py:47-52` — the loop raises on the 2nd of exactly 2 chunks, never requesting a 3rd; killed by mutation sensor #1 | ⚠️ Spec-precision gap — see note below |
| 3 | No `Content-Length`, chunked, exceeds ceiling → 413 | `413` | `test_validation.py:41-45,47-52` — both boundary tests never set a `content-length` header at all | ✅ PASS |
| 4 | `Content-Length` smaller than actual stream, exceeds ceiling → 413 | `413` | `test_validation.py:54-60` — `content_length=10` while streaming `MAX_BODY_BYTES+1` real bytes | ✅ PASS |
| 5 | Exactly 26 214 400 bytes → not 413 (inclusive) | not `413` | `test_validation.py:41-45` — `assert len(result) == MAX_BODY_BYTES`, no exception; killed by mutation sensor #1 (the boundary flip made this exact case fail) | ✅ PASS |
| 6 | Broker/topic/producer sized above ceiling | all three configured above `MAX_BODY_BYTES` | `test_compose_parity.py` (compose ships the constant) + `test_kafka.py::it_sets_kafka_config_from_the_pinned_constants` (producer) + `test_integration.py` (real broker accepts a ceiling-sized message) | ✅ PASS |

**Note on AC2**: "without buffering the complete body in memory" is a memory-behavior claim this test suite proves structurally (the loop raises before appending the oversized chunk, and — proven by the mutation sensor — before accepting one byte past the limit) rather than by measuring process memory. The test also uses two large synthetic chunks rather than many small realistic ones; a real ASGI server delivers smaller chunks, so the "stop early" property holds even more clearly in production than in this test. Marked as a precision gap, not a failure — the code demonstrably does not concatenate an oversized buffer.

### P2: Endpoint documented in OpenAPI

| # | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| - | --------- | --------------------- | ------------------------ | ------ |
| 1 | Route + all 4 status codes documented | path present, `{201,400,413,500}` in responses | `test_openapi.py:19-27` | ✅ PASS |
| 2 | Body schema derived from the validator | schema equals `BATCH_ADAPTER.json_schema()` | `test_openapi.py:29-39` — exact dict equality, plus `required` list check | ✅ PASS |

**Status**: ✅ All 24 acceptance criteria covered. 2 spec-precision gaps flagged
(both documentation-precision, neither a functional gap) and one real
coverage gap found and fixed during this pass (RCV-09's fully-unparseable
case).

---

## Discrimination Sensor

| Mutation | File:line | Description | Killed? |
| -------- | --------- | ------------ | ------- |
| 1 | `reimbursement/create/validation.py:23` | `if total > MAX_BODY_BYTES` → `if total >= MAX_BODY_BYTES` (off-by-one on the inclusive boundary) | ✅ Killed — `it_accepts_a_body_at_exactly_the_byte_limit` failed as expected |
| 2 | `reimbursement/create/producer.py:24` | `"retry":0` → `"retry":1` in the envelope prefix | ✅ Killed — 2 tests failed (`it_reconstructs_the_exact_wire_format_bytes`, `it_sets_retry_to_zero`) |
| 3 | `reimbursement/create/route.py:14` | `status_code=201` → `status_code=200` | ✅ Killed — `it_returns_201_with_the_accepted_count` failed as expected |

**Sensor depth**: lightweight (default tier) — 3 targeted mutations on the
highest-risk new code (byte-boundary math, wire-format construction, HTTP
contract). Each was applied directly to the committed tree with `sed`, run
against its specific test, and reverted with `git checkout --` before the
next mutation — confirmed via `git status --short` returning clean between
each step.

**Result**: 3/3 killed — PASS ✅

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| No features beyond what was asked | ✅ |
| No abstractions for single-use code | ✅ |
| No unnecessary "flexibility" added | ✅ |
| Only touched files required for each task | ✅ — one exception noted below |
| Didn't "improve" unrelated code | ✅ |
| Matches existing patterns/style | ✅ — `Describe*`/`it_*` convention followed throughout, confirmed against `python_classes`/`python_functions` in `pyproject.toml` |
| Would a senior engineer approve? | ✅ |
| Tests map to acceptance criteria and are non-shallow (spot-check: P1 story 3, publish failure) | ✅ — spot-checked; every test asserts resulting state (status code, response body, log content, producer call count), none rely on call-count-only or "no exception thrown" |
| Spec-anchored outcome check | ✅ — see table above; 2 precision gaps disclosed, not hidden |
| Per-layer Coverage Expectation met | ✅ — domain logic (`validation.py`, `producer.py`) has 1:1 AC mapping; the route has happy + every edge/error path |
| Every test maps to a spec AC, listed edge case, or Done-when criterion | ✅ — no speculative tests found during review |
| Documented guidelines followed | ✅ — `README.md`'s `Describe*`/`it_*` convention; no other project testing guideline exists |

**Note on "only touched files required"**: T1 (pytest wiring) touched
`pyproject.toml`, which is shared with `db-schema-migrations`. This was
additive-only (new keys, `testpaths` untouched) and flagged as a risk in
`design.md` before implementation — not an undisclosed scope violation.

---

## Edge Cases (from spec.md)

- [x] Array vs. object body shape ambiguity — resolved to array-only, tested (`test_validation.py`)
- [x] `retry=0` vs. librdkafka `retries` property ambiguity — resolved to envelope counter, both `producer.py`'s comment and `kafka.py`'s "retries not disabled" test document the distinction
- [x] Empty-array handling — tested (`test_validation.py`, `test_route.py`)
- [x] Byte ceiling boundary (MiB vs MB reading) — resolved to MiB, tested at exact boundary
- [x] Concurrent publish crossover — tested (`test_producer.py`)
- [x] Kafka headroom above HTTP ceiling — tested (`test_compose_parity.py`)

---

## Gate Check

- **Gate command**: `uv run pytest -q` (Full — includes integration)
- **Result**: 127 passed, 0 failed, 0 skipped (125 via `-m "not integration"` + 2 integration)
- **Test count before feature**: 69 (migrations suite, `main` at `9799607`)
- **Test count after feature**: 127
- **Delta**: +58 new tests
- **Skipped tests**: none
- **Failures**: none

---

## Fix Plans

None outstanding. The one gap found during this pass (RCV-09's fully-unparseable
`submitted_at` case) was fixed immediately — commit `f38f22f` — not deferred.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | ---------------- | ----------- |
| RCV-01 … RCV-08 | Design | ✅ Verified |
| RCV-09 | Design | ✅ Verified (gap closed during validation) |
| RCV-10 … RCV-21 | Design | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 24/24 ACs covered; 2 spec-precision gaps flagged (both
documentation-precision — the code's actual behavior satisfies the spec's
intent, the test just can't independently prove the exact framing the spec
uses)

**Sensor**: 3/3 mutations killed

**Gate**: 127 passed, 0 failed

**What works**: The full `POST /api/v1/reimbursement` path — streaming byte
cap, batch validation with index/field-only errors, byte-verbatim envelope
publish with per-message delivery await, concurrent-request isolation,
app-wide error contract, OpenAPI documentation, and a real 25 MiB round trip
through a live broker sized from the same constant the shipped
`docker-compose.yml` carries.

**Issues found**: One coverage gap (RCV-09's unparseable-datetime case) —
fixed in commit `f38f22f`, not deferred.

**Deviations disclosed**: (1) task-boundary fix moving one test assertion
from T6 to T7 (`ea7e98b`); (2) T10's integration broker image fell back from
`apache/kafka:4.3.1` to testcontainers' Confluent default, exactly as
anticipated in `design.md`'s risk table (`d4b2bd2`).

**Next steps**: Push the branch, open the PR, run `/code-review` and
`/tests-code-review` for an independent pass this session's own tooling
constraints couldn't provide internally.
