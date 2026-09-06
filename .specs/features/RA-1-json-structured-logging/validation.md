# JSON Structured Logging & Request Correlation Validation

**Date**: 2026-09-06
**Spec**: `.specs/features/RA-1-json-structured-logging/spec.md`
**Diff range**: `66f637d..HEAD` (18 commits, branch `feature/RA-1_json-structured-logging`)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status | Notes |
| ---- | ------ | ----- |
| T1 | ✅ Done | `ecs-logging>=2.3.0` added to `packages/shared/pyproject.toml` |
| T2 | ✅ Done | `LoggingConfig` + `Config.logging` + `load_config()` extension |
| T3 | ✅ Done | ContextVar + `get/set/reset_correlation_id` |
| T4 | ✅ Done | `CorrelationIdFilter` |
| T5 | ✅ Done | `configure_logging()` |
| T6 | ✅ Done | `log_event()` refactored to `extra={}` + UUID coercion |
| T7 | ✅ Done | `CorrelationIdMiddleware` (new file) |
| T8 | ✅ Done | Middleware registered + `configure_logging()` in `api/main.py` |
| T9 | ✅ Done | `api/migrate.py` calls `configure_logging()` |
| T10 | ✅ Done | `correlation_id` field on both envelopes |
| T11 | ✅ Done | `producer.py` byte-splice includes `correlation_id` |
| T12 | ✅ Done | `publisher/processing.py handle_message()` sets/resets ContextVar |
| T13 | ✅ Done | `publisher/processing.py _requeue()` carries `correlation_id` forward |
| T14 | ✅ Done | `reimbursement/validation.py handle_message()` sets/resets ContextVar |
| T15 | ✅ Done | Both `consumer.py` files call `configure_logging()` |
| T16 | ✅ Done | `agent.decide()` adds `correlation_id` to LangFuse metadata |
| T17 | ✅ Done | root `CLAUDE.md` "Logging" section added |

All 17 tasks implemented across 18 commits (17 feature commits + 1 style fix for an import-sort violation the author introduced and fixed in the same branch).

**Note on task-path discrepancy (per known context #1)**: tasks.md's Test Coverage Matrix cites `packages/reimbursement/tests/agent/test_agent.py` for T16's gate check; the real path is `packages/reimbursement/tests/test_agent.py` (no `agent/` subdirectory). Confirmed and used the real path throughout this validation.

---

## Spec-Anchored Acceptance Criteria

### P1: JSON Structured Logging Foundation

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| LOG-01: `configure_logging()` called once per service | Single call replaces `basicConfig` in api/main.py, api/migrate.py, publisher/consumer.py, reimbursement/consumer.py | `packages/api/tests/test_main.py:74-85` `it_calls_configure_logging_on_module_import` — `assert calls == [()]` (api/main.py, test-proven). `packages/api/src/api/migrate.py:96`, `packages/publisher/src/publisher/consumer.py:143`, `packages/reimbursement/src/reimbursement/consumer.py:103` (source-verified — one-line substitution, no dedicated test; matches tasks.md T9/T15's explicit "Tests: none, Gate: build" scoping) | ✅ PASS |
| LOG-02: ECS JSON formatter attached to root logger | Handler's formatter is `ecs_logging.StdlibFormatter`, carrying `CorrelationIdFilter` | `packages/shared/tests/test_logging.py:211-223` `it_attaches_a_handler_carrying_the_ecs_formatter_and_the_correlation_filter` — asserts `isinstance(h.formatter, ecs_logging.StdlibFormatter)` and `any(isinstance(f, CorrelationIdFilter) ...)`. Also `packages/api/tests/test_main.py:87-93` | ✅ PASS |
| LOG-03: `api/migrate.py` also uses `configure_logging()` | `basicConfig(...)` replaced by `configure_logging()` | `packages/api/src/api/migrate.py:96` (source-verified only, no dedicated test — same tasks.md T9 scoping as LOG-01) | ✅ PASS (code-verified) |
| LOG-04: valid `LOG_LEVEL` sets root logger level | Exact level constant for each of debug/info/warning/error/critical, case-insensitive | `packages/shared/tests/test_logging.py:142-162` parametrized `it_sets_the_root_logger_to_each_valid_level_case_insensitively` — `assert logging.getLogger().level == expected` for all 7 cases | ✅ PASS |
| LOG-05: unset `LOG_LEVEL` defaults to `debug` | root logger level == `logging.DEBUG` | `packages/shared/tests/test_logging.py:164-169` `it_defaults_to_debug_when_log_level_is_unset` | ✅ PASS |
| LOG-06: `log_event()` uses `extra={}`, not message-string JSON | `event`/fields appear as top-level `LogRecord` attributes; UUIDs coerced to `str` | `packages/shared/tests/test_logging.py:226-270` `DescribeLogEvent` (message content, extra field, non-UUID passthrough, UUID coercion) | ✅ PASS |
| LOG-07: invalid `LOG_LEVEL` falls back to `debug` | root logger level == `logging.DEBUG` on e.g. `"bogus"` | `packages/shared/tests/test_logging.py:171-182` `it_falls_back_to_debug_and_warns_once_on_an_invalid_level` | ✅ PASS |
| LOG-08: exactly one warning line naming the invalid value | `len(warnings) == 1` and the invalid value's text appears in it | Same test: `assert len(warnings) == 1`; `assert "bogus" in warnings[0].getMessage()` | ✅ PASS |

### P1: HTTP-Scoped Correlation ID

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| CORR-01: mint `uuid.uuid7()` when header absent | Generated id has UUID version 7 | `packages/api/tests/test_middleware.py:44-54` `it_generates_a_fresh_uuid7_when_the_header_is_absent` — `assert UUID(seen["cid"]).version == 7` | ✅ PASS |
| CORR-02: echo non-empty inbound `X-Request-ID` | Adopted value equals header verbatim | `packages/api/tests/test_middleware.py:56-67` `it_echoes_the_inbound_x_request_id_header` — `assert seen["cid"] == "caller-supplied-id"` | ✅ PASS |
| CORR-03: empty-string header treated as absent | Fresh id minted (not empty string) | `packages/api/tests/test_middleware.py:69-81` `it_treats_an_empty_header_value_as_absent_and_generates_a_fresh_one` | ✅ PASS |
| CORR-04: `X-Request-ID` returned on every response (success **or error**) | Response header present and matches the request's `correlation_id` in both cases | Success case: `packages/api/tests/test_middleware.py:83-91` and `packages/api/tests/test_main.py:97-107` (both `GET /health`, 200 responses). Error case: `packages/api/tests/test_main.py:109-121` `it_returns_the_x_request_id_header_on_an_error_response_too` — `GET /api/v1/reimbursement/not-a-uuid` triggers a real `RequestValidationError` caught by `api/errors.py:108`'s registered handler (`_validation_handler`, `errors.py:73-79`), producing a genuine 400 via `_msg_response`; `assert response.status_code == 400` and `assert response.headers["x-request-id"] == "caller-error-id"` | ✅ PASS |
| CORR-05: ContextVar + Filter auto-inject, no per-call-site `extra={}` | Attribute present/absent on `LogRecord` based on ContextVar state | `packages/shared/tests/test_logging.py:93-119` `DescribeCorrelationIdFilter` (sets when set; omits when unset; always returns `True`) | ✅ PASS |
| CORR-06: no cross-request leakage under concurrency | Each concurrent request's captured id matches only its own | `packages/api/tests/test_middleware.py:111-131` `it_never_leaks_one_concurrent_requests_id_into_anothers_scope` — `assert seen == {"a": "req-a", "b": "req-b"}` | ✅ PASS |

### P1: Kafka Correlation ID Propagation

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| CORR-07: `correlation_id` field on both envelopes | `Optional[str] = None`, present on model | `packages/shared/src/shared/models.py:101,119` (source); round-tripped in `packages/api/tests/reimbursement/create/test_producer.py:104-118` and `packages/publisher/tests/test_processing.py:295-317` (`assert set(published) == {..., "correlation_id", ...}`) | ✅ PASS |
| CORR-08: API sets it on publish from ContextVar, incl. byte-spliced prefix | Published envelope's `correlation_id` equals current ContextVar value; `None`→`null`, string→quoted | `packages/api/tests/reimbursement/create/test_producer.py` `DescribeBuildEnvelope::it_serializes_a_none_correlation_id_as_the_bare_json_null` / `it_serializes_a_string_correlation_id_as_a_quoted_json_string`; `DescribePublishCorrelationId::it_forwards_the_current_correlation_id_into_the_published_envelope` / `it_omits_the_correlation_id_when_none_is_set` / `it_never_mixes_up_two_concurrent_publishes_correlation_ids` | ✅ PASS |
| CORR-09: publisher's requeue path carries `correlation_id` forward unchanged | Requeued envelope's `correlation_id` equals the original's | `packages/publisher/tests/test_processing.py` `DescribeTheRequeue::it_carries_forward_the_original_correlation_id_unchanged` — `assert requeued["correlation_id"] == "corr-original"` (confirmed by discrimination sensor — mutation 5) | ✅ PASS |
| CORR-10: consumers set it into their own ContextVar before processing/logging | `get_correlation_id()` returns the envelope's value during processing | `packages/publisher/tests/test_processing.py` `DescribeHandleMessageCorrelationId` (4 tests: sets, resets, omits-on-None, no-leak-across-sequential-messages); `packages/reimbursement/tests/test_validation.py` `DescribeHandleMessageCorrelationId` (same 4-test shape) | ✅ PASS |
| CORR-11: `None` correlation_id never crashes/rejects a consumer | Processing proceeds normally; field omitted, not `null` | `packages/publisher/tests/test_processing.py::it_leaves_the_correlation_id_absent_when_the_envelope_carries_none` and `::it_omits_the_correlation_id_field_as_null_when_the_request_envelope_had_none`; `packages/shared/tests/reimbursement/use_cases/test_publish_pending.py::it_omits_a_correlation_id_only_as_a_null_field_never_a_crash` | ✅ PASS |

**Known-context item #2 verification**: `shared.reimbursement.use_cases.publish_pending.publish_pending()` was independently confirmed to forward `correlation_id` end-to-end on the *normal* (non-requeue) path — `RequestEnvelope.correlation_id` (set via `set_correlation_id()` in `publisher/processing.py:115`) is read back as `envelope.correlation_id` and passed positionally into `publish_pending(..., envelope.correlation_id)` at `packages/publisher/src/publisher/processing.py:305-313`, which then sets it on the constructed `ReimbursementEnvelope` at `packages/shared/src/shared/reimbursement/use_cases/publish_pending.py:72-78`. Proven end-to-end by `packages/publisher/tests/test_processing.py::it_carries_the_request_envelopes_correlation_id_onto_the_published_message` (asserts `published["correlation_id"] == "corr-request-1"` on the *published Reimbursement message*, not just the intermediate call). This closes the loop the known-context note flagged.

### P1: LangFuse Trace Correlation

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| CORR-12: `correlation_id` added to LangGraph metadata alongside `langfuse_session_id` | Both keys present with exact values | `packages/reimbursement/tests/test_agent.py::it_includes_the_correlation_id_in_metadata_when_one_is_set` — `assert calls[0]["metadata"] == {"langfuse_session_id": str(uuid), "correlation_id": "corr-decide-1"}` | ✅ PASS |
| CORR-13: unavailable id omits the metadata key (never `None`/raise) | `"correlation_id"` key absent, not `None` | `packages/reimbursement/tests/test_agent.py::it_omits_the_correlation_id_key_entirely_when_none_is_set` — `assert calls[0]["metadata"] == {"langfuse_session_id": str(uuid)}`; `assert "correlation_id" not in calls[0]["metadata"]` | ✅ PASS |
| CORR-14: no signature change to `agent.decide()` | Same parameters as before | Source diff (`packages/reimbursement/src/reimbursement/agent/agent.py`): only the function body changed (`metadata` dict construction); all existing call sites (unit tests + `reimbursement/validation.py`) continue to call it with the same signature, unmodified | ✅ PASS (source-verified) |

### P3: Logging Convention Documentation

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| DOC-01: "Logging" section in root `CLAUDE.md` | Covers `configure_logging()` at every entrypoint, `LOG_LEVEL` default/fallback, full `correlation_id` chain | `/CLAUDE.md:19-26` — new "## Logging" section with 4 bullets covering exactly these 3 required elements plus `log_event()` conventions (content read and confirmed verbatim) | ✅ PASS |

**Status**: 23/23 ACs fully covered and spec-precise. (Iteration 2: CORR-04's error-response half is now proven — see commit `197dfdc`.)

---

## Discrimination Sensor

All mutations injected directly into the real tracked files, confirmed killed, then reverted via `git checkout -- <file>` (verified clean via `git diff --stat` after each revert — zero residual diff). No scratch worktree was needed since every mutation was reverted before the next was applied; the real tree was never left mutated between steps.

| # | File:line | Mutation | Killed? |
| - | --------- | -------- | ------- |
| 1 | `packages/shared/src/shared/logging.py:61` | `CorrelationIdFilter.filter()`: `if correlation_id is not None` → `if correlation_id is None` | ✅ Killed — 3 tests failed in `DescribeCorrelationIdFilter` |
| 2 | `packages/shared/src/shared/logging.py:89` | `configure_logging()`: `if resolved is None` → `if resolved is not None` (inverts valid/invalid branch) | ✅ Killed — 7 tests failed in `DescribeConfigureLogging` |
| 3 | `packages/api/src/api/middleware.py:31` | `_extract_or_generate_correlation_id()`: `if decoded:` → `if not decoded:` (inverts empty/non-empty header handling) | ✅ Killed — 4 tests failed in `test_middleware.py` |
| 4 | `packages/shared/src/shared/logging.py:117` | `log_event()`: removed `str(v) if isinstance(v, UUID) else v` UUID coercion | ✅ Killed — 1 test failed (`it_coerces_a_uuid_field_to_a_plain_string_not_its_repr`) |
| 5 | `packages/publisher/src/publisher/processing.py:340` | `_requeue()`: removed explicit `correlation_id=envelope.correlation_id` (falls back to model default `None`) | ✅ Killed — 1 test failed (`it_carries_forward_the_original_correlation_id_unchanged`) |
| 6 (iteration 2, targeted) | `packages/api/src/api/middleware.py:51` | `send_wrapper()`: removed `headers.append((_HEADER_NAME, correlation_id.encode("latin-1")))` | ✅ Killed — all 3 tests in `DescribeCorrelationIdMiddlewareRegistration` failed (`test_main.py`), including the new error-response test |

**Sensor depth**: lightweight (5 targeted mutations at iteration 1, per feature's non-P0 tiering; +1 targeted re-verification mutation at iteration 2 scoped to the single new test)
**Result**: 6/6 killed — ✅ PASS

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ — every task's diff matches its stated scope; no speculative abstractions |
| Surgical changes | ✅ — only files named in tasks.md touched (confirmed via `git diff --stat`) |
| No scope creep | ✅ — no PII fields added, no persistence/migration added, no Kafka-header plumbing added (matches spec's Out of Scope table) |
| Matches patterns | ✅ — `LoggingConfig` mirrors existing `KafkaConfig`/`DatabaseConfig` shape; `CorrelationIdMiddleware` mirrors `api/errors.py`'s small-single-purpose-module precedent |
| Spec-anchored outcome check (asserted values match spec) | ✅ for 23/23 (CORR-04 closed at iteration 2) |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ — `api.middleware`/`api.main` now covers both the success and error branch for CORR-04; every layer meets 1:1 |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — every new test traces to a LOG-*/CORR-*/DOC-* ID or a named Edge Case (idempotency, collision handling, None-safety) |
| Documented guidelines followed | ✅ — pytest `Describe*`/`it_*` style (root `CLAUDE.md`) followed throughout; `AD-023`'s "plain data holder" config pattern followed for `LoggingConfig` |

---

## Edge Cases

- [x] `configure_logging()` called more than once: no duplicate handlers — `test_logging.py::it_attaches_the_handler_only_once_across_repeated_calls`
- [x] `log_event()` field collision with reserved attribute: falls back without raising — `test_logging.py::it_falls_back_without_raising_when_a_field_collides_with_a_reserved_attribute`
- [x] Code outside HTTP/Kafka scope logs with no `correlation_id`: default `ContextVar` value is `None`, `CorrelationIdFilter` omits the attribute — proven generically by `DescribeCorrelationIdFilter::it_omits_the_attribute_entirely_when_no_correlation_id_is_set`; migrate.py/lifespan itself not separately exercised (low risk — no new branching there)
- [x] `ecs-logging` declared in `packages/shared/pyproject.toml` only — confirmed via `git diff --stat` (single dependency line added, no other package's `pyproject.toml` touched)
- [x] Long/unsafe `X-Request-ID` values rely on JSON formatter escaping, no new sanitization added — confirmed by inspection: `middleware.py` does no length/character validation, consistent with spec's explicit "rely on the formatter" decision (nothing new to test)

---

## Gate Check

- **Gate command**: `uv run pytest packages/api packages/publisher packages/reimbursement packages/shared tests/e2e -v && uv run ruff check packages/ && uv build`
- **pytest result**: 646 passed, 0 failed, 8 deselected (pre-existing `-m "not e2e"` addopts scoping in root `pyproject.toml`, unrelated to this feature)
- **ruff result**: 71 findings — independently verified every finding in every file this feature's diff touches (13 touched files carry findings) is pre-existing: same rule code and same finding count on `main` for each file, with only line-number shifts consistent with this feature's added lines (verified via `git show main:<file> | uv run ruff check --stdin-filename=<file> -` for all 13 files). **Zero net-new violations.** The one new violation the author introduced mid-branch (import-sort in `test_middleware.py`) was fixed in commit `c525911` and confirmed absent from current findings.
- **uv build result**: succeeded — `reimbursementanalyzer-0.1.0.tar.gz` and `.whl` both built cleanly
- **Test count before feature**: not independently re-collected against the pre-feature commit (would require re-installing a divergent dependency set — `ecs-logging` didn't exist as a dependency yet — out of proportion to this feature's actual test-execution scope). Delta computed via diff instead (below).
- **Test count after feature**: 646 passed (within the four in-scope packages + e2e paths)
- **Delta (via diff)**: +55 new `it_`/`test_` functions added, 1 removed — but the removed one (`test_main.py`'s `it_calls_basic_config_at_info_level_on_module_import`) has a direct, verified replacement in the same diff hunk (`it_calls_configure_logging_on_module_import`, testing the equivalent post-refactor behavior) plus one net-new test alongside it. **Zero silent deletions.**
- **Skipped tests**: none (the 8 deselected are e2e-marked tests excluded by pre-existing `addopts`, not skips)
- **Failures**: none

---

## Fix Plans

### Fix 1: CORR-04's error-response half has no test coverage — ✅ RESOLVED (iteration 2)

- **Root cause**: `packages/api/tests/test_middleware.py` and `packages/api/tests/test_main.py` only exercised the success path (`GET /health`, 200) for `X-Request-ID` response-header injection. No test sent a request that triggered one of `api/errors.py`'s 11 registered exception handlers and asserted the header was still present on that response.
- **Risk assessment**: Confirmed to be a test-coverage gap only, not a functional defect — the fix (a new test, no production code change) proves the behavior already worked, matching the architectural review from iteration 1.
- **Resolution**: Commit `197dfdc` ("test(api): cover CORR-04's error-response half of X-Request-ID header") adds `it_returns_the_x_request_id_header_on_an_error_response_too` to `packages/api/tests/test_main.py:109-121`. It sends `GET /api/v1/reimbursement/not-a-uuid`, which FastAPI's path-param coercion rejects with `RequestValidationError`, caught by `api/errors.py:108`'s registered `_validation_handler` (a real 400 via `_msg_response`, no mocking). Asserts both `response.status_code == 400` and `response.headers["x-request-id"] == "caller-error-id"`. Verified non-shallow: a targeted mutation removing the header-injection line in `middleware.py`'s `send_wrapper` kills this test (and the other two in its class) — see Discrimination Sensor mutation 6.
- **Priority**: Resolved.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | --------------- | ---------- |
| LOG-01 | Implementing | ✅ Verified |
| LOG-02 | Implementing | ✅ Verified |
| LOG-03 | Implementing | ✅ Verified |
| LOG-04 | Implementing | ✅ Verified |
| LOG-05 | Implementing | ✅ Verified |
| LOG-06 | Implementing | ✅ Verified |
| LOG-07 | Implementing | ✅ Verified |
| LOG-08 | Implementing | ✅ Verified |
| CORR-01 | Implementing | ✅ Verified |
| CORR-02 | Implementing | ✅ Verified |
| CORR-03 | Implementing | ✅ Verified |
| CORR-04 | Implementing | ✅ Verified |
| CORR-05 | Implementing | ✅ Verified |
| CORR-06 | Implementing | ✅ Verified |
| CORR-07 | Implementing | ✅ Verified |
| CORR-08 | Implementing | ✅ Verified |
| CORR-09 | Implementing | ✅ Verified |
| CORR-10 | Implementing | ✅ Verified |
| CORR-11 | Implementing | ✅ Verified |
| CORR-12 | Implementing | ✅ Verified |
| CORR-13 | Implementing | ✅ Verified |
| CORR-14 | Implementing | ✅ Verified |
| DOC-01 | Implementing | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 23/23 ACs matched spec outcome precisely (CORR-04 closed at iteration 2)
**Sensor**: 6/6 mutations killed (5 at iteration 1, +1 targeted re-verification mutation at iteration 2)
**Gate**: 646 passed, 0 failed (8 pre-existing e2e deselections) at iteration 1; iteration 2's scoped re-run (`packages/api/tests/test_main.py`) adds 1 new test, 10/10 passed, 0 failed, no regressions. ruff 0 net-new violations (71 pre-existing, independently confirmed); `uv build` succeeded.

**What works**: All JSON structured logging (LOG-01..08), HTTP-scoped correlation ID including both the success and error response paths (CORR-01..06), Kafka correlation propagation including the requeue path and the previously-uncalled-out `publish_pending()` forwarding gap (CORR-07..11), LangFuse metadata wiring (CORR-12..14), and documentation (DOC-01) are fully implemented, precisely tested, and survived targeted fault injection with zero surviving mutants. Ruff is clean of any new violations this feature introduced.

**Issues found**: None outstanding. Iteration 1's only gap (CORR-04's error-response half) was closed by commit `197dfdc`, which added one non-shallow test (confirmed via a targeted discrimination mutation) with no production code change.

**Next steps**: None — feature is fully verified. Ready to ship.
