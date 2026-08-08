# Publisher — Consume `Request`, Produce `Reimbursement` — Validation (Round 2)

**Date**: 2026-08-08
**Spec**: `.specs/features/publisher-consume-request/spec.md` (amended at `11747a5`)
**Design**: `.specs/features/publisher-consume-request/design.md`
**Tasks**: `.specs/features/publisher-consume-request/tasks.md`
**Diff range**: `e334127..HEAD` — 22 commits, `47dd840` … `11747a5`
(implementation `47dd840`…`1691e8d`, fix round `2b633c2`…`11747a5`), branch
`feature/5_reimbursement_publisher`
**Verifier**: independent sub-agent (author ≠ verifier). The verifier authored
neither the implementation nor the fixes; every claim below was re-derived from
the tree, and no fix agent's assertion was taken on trust.
**Round**: 2 of a maximum 3.

**Overall verdict: ✅ PASS** — 42/42 acceptance criteria covered with cited
evidence, both round-1 surviving mutants now killed, 18 further behaviour faults
injected in this round and all killed by the test that owns the claim, and no
regression in the 34 criteria that already passed. Two non-blocking observations
are recorded at the end.

---

## Round-1 → Round-2 Delta

| | Round 1 | Round 2 |
| - | ------- | ------- |
| ACs covered | 34 | **42** |
| ACs weak | 4 (PUB-14, 21, 22, 35) | **0** |
| ACs uncovered | 4 (PUB-15, 33, 39, 42) | **0** |
| Mutants killed | 15/18 (1 partial, 2 survived) | **18/18** |
| Quick gate | 258 passed / 6 deselected | **268 passed / 6 deselected** |
| Full gate | 264 passed | **274 passed** |
| Verdict | ❌ FAIL | **✅ PASS** |

---

## Gate Check

| Gate | Command | Result |
| ---- | ------- | ------ |
| Quick | `uv run pytest -q -m "not integration"` | **268 passed, 6 deselected** in 8.6 s |
| Full | `uv run pytest -q` | **274 passed** in 46.7 s |

**Test Integrity Check** — the fix round is **+11 tests added, −1 deleted**
(net +10 Quick, +10 Full). The single deletion is
`test_errors.py::DescribeDuplicateRequest::it_is_a_catchable_exception`, removed
alongside the `DuplicateRequest` class it covered (round-1 gap #9: dead code,
never raised or caught in `src/`). Justified, and `design.md:312-315` was updated
to record that duplicates are classified by `repository.is_duplicate(exc)` on the
driver's own exception instead. Zero skips in either run.

**Assertion-weakening check.** Two pre-existing assertions were narrowed:
`test_processing.py:102` and `:160` changed from positional
`[row[0] for row in pool.inserted] == [...]` to `sorted(...) == [...]`. This is
**not** a weakening — the exact set and exact count of inserted `request_id`s are
still asserted; what was dropped is an ordering claim over `pool.inserted`, which
is appended **on completion** and which PUB-39 explicitly declares
non-deterministic. Round 1 flagged those very assertions as depending on the
ordering the spec forbids relying on. Ordering is still pinned where the spec
*does* specify it — outcomes in submission order — and mutant **N7** proves it.
`test_repository.py`'s `_rows_for` widened from `SELECT uuid` to `SELECT *` to
support the new content assertions; no assertion was removed.

---

## Judgement on the Three Spec Amendments

The brief asked for a plain read on whether these were legitimate corrections or
convenient redefinitions. Taken one at a time:

### PUB-33 — termination semantics reversed → **legitimate correction, and the strongest of the three**

The original AC required an in-flight transaction to roll back with its offset
uncommitted. The code never did that, and `design.md:388,448` had described the
opposite (graceful drain) since the design phase — the AC was the artefact left
behind, not a promise the code broke. The amendment reverses the AC to match, and
says so in the Assumptions table verbatim ("*this **reverses PUB-33's original
wording***"), with the reasoning that draining avoids discarding work that already
succeeded and that correctness under an *abrupt* kill (SIGKILL, OOM, node loss) is
unchanged either way, still resting on redelivery plus the duplicate path per
**R-001**.

Crucially the amendment made the AC **harder**, not easier: it replaced a negative
that no test had pinned down with two positive, observable claims ("complete and
commit its offset", "exit without consuming another message"), and coverage went
*up* — a real-`SIGTERM` test now exists where round 1 found none, and both halves
are independently killed (M9, N10). This is not a dodge.

### PUB-35 — fetch-sizing claim withdrawn → **legitimate correction of a false premise**

Round 1's finding was empirical: mutant M8 reverted both consumer fetch limits to
librdkafka's 1 MiB default and the ceiling-sized integration test stayed green, so
the test could not be proving what the AC claimed. The coordinator reports the
KIP-74 reading was independently confirmed against the Apache KIP-74 page and the
librdkafka commit — `fetch.max.bytes` is a soft limit and the broker always
returns at least one record per partition, so a consumer cannot stall on a single
oversized record and the original AC was **unprovable because it was false**.

The response was the honest shape: the settings are **kept**, PUB-34's structural
assertion is **unchanged** (and mutant M8 still kills it), PUB-35 is restated as
the thing that *is* observable, the "Why P1" prose is rewritten to the real
justification rather than deleted, and the disproven premise is written into the
Assumptions table so it cannot be silently re-added. Retreating to a smaller true
claim after disproving a larger one is correction, not redefinition.

*Residual, stated plainly*: the **new** rationale — that the fetch limits bound a
**multi-message** fetch — is itself asserted by no test. It is a correct reading of
librdkafka's semantics, but the suite proves only the config value (PUB-34) and the
single-message round trip (PUB-35). The AC no longer claims it, so this is honest
scoping rather than a gap; it is recorded as observation #1 below.

### PUB-42 — wall-clock promise withdrawn → **a real narrowing, declared openly; defensible but the weakest of the three**

The original AC ("completes well within `max.poll.interval.ms`") named no
threshold and no measurement conditions — unfalsifiable as written, and the exact
shape of confirmed lesson **L-003**. The replacement substitutes two observable
claims: the interval is set explicitly rather than inherited, and the fan-out is
genuinely concurrent rather than serialised. Both are now asserted and both are
killed (N6, N5).

This *is* a narrowing — the system no longer promises a real 500-item batch fits
inside 300 s, and nothing in the repo demonstrates that it does. What makes it
defensible rather than a dodge is that the AC text **says so itself** ("*is the
**rationale** for those two values, not an asserted runtime — a wall-clock promise
is not observable without a load test (**R-005**)*"), and **R-005** already owns
the untested 500/10 throughput parameters. Declaring the reduced promise inside the
AC and pointing at the risk that owns the remainder is the right way to do this.

*Two honest quibbles*: (a) the residual risk is unchanged, merely relocated — it
was always R-005's and still is; (b) the substituted assertion is, despite its own
comment ("*Not a runtime budget*"), a wall-clock measurement via
`time.perf_counter` — just with a loose bound rather than a tight one. See
observation #2.

---

## Spec-Anchored Acceptance Criteria

Line numbers as of the verified HEAD. Publisher tests under
`src/publisher/tests/`, shared tests under `src/shared/tests/`.

### Previously-closed criteria — re-verified, no regression

PUB-01…PUB-13, PUB-16…PUB-20, PUB-23…PUB-32, PUB-34, PUB-36…PUB-38, PUB-40,
PUB-41 (**34 criteria**) were verified with cited evidence in round 1 and are
unaffected. Re-confirmed three ways: the Full gate is green at a *higher* count
with one justified deletion; the assertion-weakening check above; and five round-1
mutations re-run against the modified files, all still killed —

| Round-1 mutant | Target AC | Round 1 | Round 2 |
| -------------- | --------- | ------- | ------- |
| M2 — publish moved outside `conn.transaction()` | PUB-09 | ✅ killed (1) | ✅ killed (1) |
| M3 — `Semaphore(item_concurrency)` → `Semaphore(1000)` | PUB-36 | ✅ killed (1) | ✅ killed (1) |
| M7 — error history replaced, not appended | PUB-12 | ✅ killed (1) | ✅ killed (2) |
| M13 — publish attempted despite a failed insert | PUB-01/08 | ✅ killed (3) | ✅ killed (3) |
| M14 — `sanitize` leaks the driver `DETAIL` | PUB-15 | ✅ killed (5) | ✅ killed (6) |

### The eight criteria under re-verification

| AC | Spec-defined outcome (amended text where applicable) | `file:line` + assertion | Result |
| -- | --------------------------------------------------- | ----------------------- | ------ |
| PUB-14 | A republished message is later processed exactly like any other, no special-casing beyond a non-zero `retry` | `test_processing.py:395-411` — `_requeued_raw(first)` takes the **literal bytes** the requeue put on `Request` and feeds them back to `handle_message`: `outcomes == [PUBLISHED]`, `pool.inserted == ["REQ-ROUND-TRIP"]`, one `Reimbursement` message, and `second.messages(REQUEST_TOPIC) == []`. `:413-429` drives a genuine two-hop chain: `requeued["retry"] == 2`, `[attempt…] == [1,2]`, `payload == [item]` | ✅ PASS |
| PUB-15 | stdout record carries the item's `request_id` **and** error type, and neither the payload nor the driver's `DETAIL` | Positive half now implemented at all three failure sites (`processing.py:226-232`, `:148-153`, `:180-186`, each via `_request_id(item)`). `test_processing.py:431-445` — `"REQ-IDENTIFIED" in logged`, `"ana@company.com" not in logged`, `"93.5" not in logged`; `:617-637` the escalation site; `:753-766` the invalid-item site | ✅ PASS |
| PUB-21 | The already-committed row it collided with is **unaffected** | `test_processing.py:270-274` — stored with `amount=10.0`, resubmitted with `amount=999.0`, then asserts the surviving row's `original_payload == stored`, `submitted_by == "ana@company.com"` (original casing), `status == "pending"`. `test_repository.py:84-91` adds `decision_reason is None`; `:108-110` asserts the case-varied resubmission did not rewrite `submitted_by`. The differing `amount` is what makes the assertion bite — an identical resubmission would have made it vacuous | ✅ PASS |
| PUB-22 | `retry > 3` — and only `> 3` — skips the normal flow | `test_processing.py:506-521` — at `retry == MAX_RETRY` the item takes the **normal** path (`outcomes == [PUBLISHED]`, insert happened, one `Reimbursement` message) and `assert MAX_RETRY == 3` pins the constant itself; `:523-539` — at `MAX_RETRY + 1` it escalates, real-DB `status == "human-review"`, `producer.produced == []` | ✅ PASS |
| PUB-33 | *(amended)* A termination signal ⇒ the message in hand completes and commits its offset, then the loop exits without consuming another | `test_consumer.py:313-329` — fires a **real** `signal.raise_signal(signal.SIGTERM)` from inside the producer while the first item is mid-publish and waits on `stopping` (so the handler provably fired *in flight*), then asserts `stopping.is_set()`, `pool.inserted == ["REQ-SIGTERM"]`, `commits == [message]`, `len(consume_kwargs) == 1`. A **second** batch is queued, so the last assertion is not vacuous | ✅ PASS |
| PUB-35 | *(amended)* A ceiling-sized `Request` is consumed and processed end to end — row committed, `Reimbursement` published | `test_integration.py:211-234` — real broker, `len(raw) == KAFKA_MAX_MESSAGE_BYTES - 4096` and `len(raw) > 1_048_576`, then row `status == "pending"` and exactly one drained `Reimbursement` message. The claim asserted now matches the claim made | ✅ PASS |
| PUB-39 | Completion order and publish order are non-deterministic; no behaviour depends on either | `test_processing.py:165-185` — `insert_turns={"REQ-0": 4, "REQ-1": 2}` forces completion in reverse; the test **proves its own premise** (`pool.inserted == ["REQ-2","REQ-1"]`, i.e. completion really is reordered) and then asserts `outcomes == [REQUEUED, PUBLISHED, PUBLISHED]` in **submission** order and that the requeue targeted `REQ-0`. Deterministic by event-loop turns, not by a clock — no race. The two positional assertions round 1 flagged are now `sorted(...)` | ✅ PASS |
| PUB-42 | *(amended)* `max.poll.interval.ms` set explicitly, and items processed concurrently up to the limit rather than one at a time | Half 1: `config.py:74-77,88` + `test_config.py:144-152` — `max_poll_interval_ms == 300_000` **and** `consumer_config["max.poll.interval.ms"] == 300_000`. Half 2: `test_processing.py:120-137` — 500 items × 2 ms fixed delay, `elapsed < (ceil(500/10)×delay + 500×delay)/2` = `< 0.55 s`, against ~0.1 s overlapped and ~1.0 s serialised | ✅ PASS |

**Status**: ✅ 42/42 covered · 0 weak · 0 uncovered.

---

## Deviation Scrutinised: the `_stdout_log(caplog)` helper

**Verdict: confirmed legitimate — it narrows the read to the surface PUB-15
governs, and it demonstrably does not blind the test to a real leak.**

The helper (`test_processing.py:79-85`) joins only records whose
`record.name == processing.__name__`, replacing a bare join over all
`caplog.records`.

1. **It is necessary, not cosmetic.** Two of the three new sites — the escalation
   failure and the invalid item — *do* write a failure-log record, and
   `failure_record` (`processing.py:275-282`) includes `"item": item` verbatim.
   `caplog` captures at handler level, so the CRITICAL failure record is swept in
   alongside the ERROR line. Without the filter, `"ana@company.com" not in logged`
   and `"not-an-email-at-all" not in logged` would fail on content the spec
   deliberately permits.
2. **It is spec-correct.** PUB-15 governs the stdout operational record. The
   Assumptions row (`spec.md:98`) places the failure log inside the payload's own
   trust boundary, alongside the envelope and the row — the same row that scopes
   the sanitisation requirement to stdout.
3. **The assertions still bite — verified, not assumed.** Mutant **N1** injects
   the raw item into the *`processing`* logger's own message, i.e. a real leak on
   exactly the surface the filter still reads. Two tests fail
   (`it_keeps_the_drivers_value_bearing_detail_out_of_the_stdout_log` and
   `it_identifies_the_failed_item_by_its_request_id`). Mutants **N2/N3/N4** remove
   `request_id` from each of the three call sites and each is caught by its own
   test. The filter narrows *which logger* is read; it does not soften *what* is
   asserted about it.

*Related finding, not caused by this helper*: see observation #3 below — in the
shipped runtime wiring the failure logger propagates to the same console stream,
which is a production concern the test-side filter neither creates nor hides.

---

## Discrimination Sensor — Round 2

Sensor depth: **P0-full**. 18 behaviour-level faults, designed by the verifier
from the spec — 3 re-runs of round 1's unresolved cases, 10 fresh faults aimed at
the newly-added assertions, 5 regression re-runs. Each was applied to the working
tree, exercised, then reverted with `git checkout -- src/`; `git diff -- src/` was
confirmed empty afterwards. Gate: `uv run pytest -q -m "not integration"`, plus
`uv run pytest -q src/publisher/tests/test_integration.py` for M8.

### Round-1 unresolved cases, re-run

| # | Target | File | Fault | R1 | R2 |
| - | ------ | ---- | ----- | -- | -- |
| M9 | PUB-33 | `consumer.py:87-90` | `_install_signal_handlers` reduced to `return None` | ❌ Survived | ✅ **Killed** — `test_consumer.py::DescribeTheLoop::it_stops_on_a_real_sigterm_only_after_the_message_in_hand_commits` |
| M10 | PUB-22 | `processing.py:90` | Retry ceiling `> MAX_RETRY` → `>= MAX_RETRY` | ❌ Survived | ✅ **Killed** — `…::DescribeTheRetryCeilingBoundary::it_takes_the_normal_path_at_the_ceiling_itself` |
| M8 | PUB-34 / PUB-35 | `config.py:88-90` | Both consumer fetch limits → librdkafka's 1 MiB default | ⚠️ Partial | ✅ **Killed by the test that owns the claim** — `test_config.py::…::it_sizes_both_fetch_limits_from_the_shared_message_ceiling` (PUB-34). The integration test still passes (4/4), which under the **amended** PUB-35 is now correct behaviour, not a gap: PUB-35 no longer claims fetch sizing is what makes the record readable |

### Fresh faults against the round-2 assertions

| # | Target | File | Fault | Killed? | Killed by |
| - | ------ | ---- | ----- | ------- | --------- |
| N1 | PUB-15 | `processing.py:226-232` | stdout record leaks the whole item | ✅ Killed (2) | `…::it_keeps_the_drivers_value_bearing_detail_out_of_the_stdout_log`, `…::it_identifies_the_failed_item_by_its_request_id` |
| N2 | PUB-15 | `processing.py:226-232` | requeue log drops `request_id` again | ✅ Killed | `…::DescribeTheRequeue::it_identifies_the_failed_item_by_its_request_id` |
| N3 | PUB-15 | `processing.py:148-153` | escalation log drops `request_id` | ✅ Killed | `…::it_identifies_the_item_it_could_not_escalate_by_its_request_id` |
| N4 | PUB-15 | `processing.py:180-186` | invalid-item log drops `request_id` | ✅ Killed | `…::it_is_identified_by_its_request_id_in_the_stdout_log` |
| N5 | PUB-42 | `processing.py:107-111` | Fan-out serialised — `gather` replaced by a sequential comprehension | ✅ Killed (3) | `…::it_overlaps_the_items_instead_of_taking_one_delay_each`, plus the saturation and out-of-order tests |
| N6 | PUB-42 | `config.py:88` | `max.poll.interval.ms` left to the client library | ✅ Killed | `test_config.py::…::it_states_the_poll_interval_budget_rather_than_inheriting_it` |
| N7 | PUB-39 | `processing.py:107-111` | Outcomes returned in **completion** order (`as_completed`) instead of submission order | ✅ Killed (2) | `…::it_settles_every_item_correctly_when_they_complete_out_of_order`, `…::it_leaves_the_valid_items_beside_it_unaffected` |
| N8 | PUB-14 | `processing.py:88` | A non-zero `retry` special-cased instead of reprocessed | ✅ Killed (10) | `…::it_reprocesses_the_message_it_requeued_like_any_other`, `…::it_carries_the_requeued_history_into_the_attempt_after_it`, + 8 |
| N9 | PUB-21 | `repository.py:20-24` | `ON CONFLICT … DO UPDATE` — a resubmission silently overwrites the row it collided with | ✅ Killed (6) | `test_repository.py::…::it_stores_no_second_row_for_a_repeated_request_and_submitter`, `test_processing.py::DescribeADuplicateItem::…`, + 4 |
| N10 | PUB-33 | `consumer.py:79` | Loop breaks on the signal **before** committing the drained message | ✅ Killed (2) | `…::it_stops_on_a_real_sigterm_only_after_the_message_in_hand_commits`, `…::it_lets_the_message_in_hand_finish_and_commit_before_it_stops` |

**Result**: **18/18 killed** by the test that owns the corresponding claim —
✅ PASS. No mutant survived in this round.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ Round-1 dead code (`DuplicateRequest`) removed along with its test and its `design.md` mention |
| Surgical changes | ✅ The only production change is the three `request_id=%s` log sites plus one config field; everything else is test-side |
| No scope creep | ✅ |
| Matches patterns | ✅ `Describe*`/`it_*`, explicit `pytestmark`, fakes extended rather than duplicated (`insert_turns`, `insert_delay_seconds` on the existing `FakePool`) |
| Spec-anchored outcome check | ✅ 42/42 asserted values match the spec-defined outcome |
| Per-layer Coverage Expectation met | ✅ Domain logic 1:1 with ACs; the concurrency layer now has an observable assertion where round 1 had none |
| Every test maps to a spec requirement | ✅ The one unclaimed test found in round 1 was deleted with its dead class. `test_database_fixture.py`'s two tests map to T6's Done-when (test infrastructure — acceptable) |
| Documented guidelines followed | ✅ `docs/codebase/TESTING.md`, `docs/codebase/CONVENTIONS.md`, root `pyproject.toml` |

---

## Edge Cases

- [x] Two instances on different partitions rely on consumer-group assignment + the DB unique constraint (`repository.py:77-84`, `test_repository.py:136-166`).
- [ ] `submitted_at` more than one hour in the future routed as a standard non-duplicate DB failure — still no dedicated test; covered only by the generic non-duplicate branch. Unchanged from round 1, and explicitly an inherited gap owned by **R-002**.
- [x] Crash between broker ack and DB commit — accepted as **R-001**, out of scope.
- [x] Broker unreachable at consume time — delegated to librdkafka, no code to assert.
- [x] The same error on all four attempts renders four entries (`test_send_human_review.py:51-59`).
- [x] Processing exceeding `max.poll.interval.ms` — the interval is now explicitly stated and the fan-out is proven concurrent; the residual wall-clock question is owned by **R-005** per the amended PUB-42.

---

## Non-Blocking Observations

None of these is an AC failure or a regression; all are recorded so they are not
rediscovered.

1. **The new PUB-35 rationale is unasserted.** The amendment justifies keeping the
   fetch limits by saying they bound a **multi-message** fetch. No test exercises a
   multi-message fetch, so the suite proves the config value (PUB-34) and the
   single-message round trip (PUB-35) but not the stated reason for the value. The
   AC does not claim it, so this is honest scoping — but the rationale rests on
   library semantics rather than evidence, and round 1 showed how that goes.
2. **`it_overlaps_the_items_instead_of_taking_one_delay_each` is a wall-clock
   assertion.** Despite its comment ("*Not a runtime budget*") it measures with
   `time.perf_counter` and compares against `0.55 s`. The margin is wide — ~0.1 s
   overlapped vs ~1.0 s serialised, threshold midway, so ~3.5–5× headroom — and the
   two shapes are an order of magnitude apart, so only genuine serialisation should
   cross it. It is nonetheless the most plausible flake in the suite on a heavily
   loaded machine, and it is the only timing-sensitive test present.
3. **In the shipped runtime wiring, the failure log lands in the same console
   stream as the sanitised log.** `consumer.py:111` calls
   `logging.basicConfig(level=logging.INFO)`; the `reimbursementanalyzer.failures` logger has
   `propagate = True` and no handler of its own, so its payload-bearing record
   propagates to the root handler. Verified empirically: writing a failure record
   under `basicConfig` puts `ana@company.com` into the captured stream. PUB-15 is
   still satisfied on a strict reading — it governs *the* stdout operational record,
   and `spec.md:98` places the failure log inside the payload's trust boundary — but
   that same row's rationale ("stdout sits outside the payload's trust boundary,
   unlike the envelope, the row, and the failure log") assumes the two are disjoint
   streams, and by default they are not. This is **pre-existing**, not introduced by
   the fix round, and not something the `_stdout_log` test helper creates or
   conceals. Cheapest fix: set `propagate = False` on the failure logger and make
   attaching a handler an explicit ops step, which is what the failure-log
   Assumptions row already envisages.
4. **Two Kafka containers still start on a Full run** (round-1 open question 2).
   Unchanged and out of scope: `src/api/tests/reimbursement/create/conftest.py:57`
   and `src/publisher/tests/conftest.py:19` each define a session-scoped
   `kafka_bootstrap_server`, and pytest caches session fixtures per definition, not
   per name. Full gate 46.7 s vs Quick 8.6 s.

---

## Requirement Traceability Update

| Requirement | Round-1 Status | Round-2 Status |
| ----------- | -------------- | -------------- |
| PUB-01…PUB-13, PUB-16…PUB-20, PUB-23…PUB-32, PUB-34, PUB-36…PUB-38, PUB-40, PUB-41 | ✅ Verified | ✅ Verified (re-confirmed, no regression) |
| PUB-14, PUB-21, PUB-22 | ⚠️ Weak | ✅ **Verified** |
| PUB-15, PUB-39 | ❌ Needs Fix | ✅ **Verified** |
| PUB-33, PUB-35, PUB-42 | ❌ Needs Fix / ⚠️ Weak | ✅ **Verified against amended AC text** |

**42 of 42 Verified.**

---

## Lessons

Round 2 produced **no new grounded signal** — no surviving mutant, no failed or
uncovered AC, no new spec-precision gap, no `SPEC_DEVIATION` marker. Per
[lessons.md](../../../../.claude/skills/tlc-spec-driven/references/lessons.md), a
clean PASS records nothing, so **no lesson was added in this round**.

The seven lessons distilled from round 1 (`L-005`…`L-010`, plus the recurrence
that promoted `L-003` to **confirmed**) stand unchanged. No confirmed lesson was
penalised: `L-003` was corroborated *by* this feature's round-1 signal rather than
loaded as guidance before it, so its failure here is not a repeat.

---

## Summary

**Overall**: ✅ Ready.

**Spec-anchored check**: 42/42 matched the spec-defined outcome · 0 weak · 0 uncovered
**Sensor**: 18/18 killed (round 1: 15/18) — both round-1 survivors now dead
**Gate**: Quick 268 passed / 6 deselected · Full 274 passed · 0 failed · 0 skipped

**What works**: every gap ranked in round 1 is closed with an assertion that
demonstrably bites, not merely a test that exists. The retry ceiling is pinned on
both sides of the boundary (M10 dead); the drain is proven by a **real** SIGTERM
delivered mid-publish, and both halves of the amended contract are separately
killable (M9, N10); `request_id` now appears at all three failure sites with the
PII exclusion still intact under a leak injected into the very logger the test
reads (N1–N4); order-independence is proven by a fake that deterministically
reverses completion and asserts it did so (N7); the duplicate path is shown to
leave the colliding row byte-identical, with a differing `amount` making the
assertion bite (N9); and PUB-14's round trip now replays the literal bytes the
requeue emitted (N8). No previously-passing criterion regressed.

**Issues found**: none blocking. Four non-blocking observations recorded above,
of which #3 (the failure logger propagating into the console stream by default)
is the one worth an owner.

**Next steps**: none required by this verification. Optionally route observation
#3 to a follow-up; observations #1, #2 and #4 are already owned by R-005 /
existing scope notes or are deliberate.
