# Traceability Correlation IDs Specification

## Problem Statement

`CLAUDE.md` makes full traceability a hard, functional requirement: every
operation that creates, modifies, or influences a reimbursement decision
must be reconstructable after the fact from durable records, without
re-running anything. Today that bar is met in exactly one place —
`reimbursement/validation.py`'s orchestrator layer and `publisher/processing.py`'s
three-tier pattern — and is missing everywhere else that matters: all four
`api` routes (`POST`/`GET` list/`GET` by uuid/`PUT`), the five agent decision
nodes (which have the uuid in scope but never log it), and the LangFuse
trace itself, which today carries no reimbursement uuid at all and cannot be
correlated back to the row it decided. This spec closes those specific gaps.

**Amendment (2026-08-10)**: after TRC-01..11 shipped and merged (PR #12),
review surfaced two further gaps in the same pre-uuid correlation chain the
Problem Statement above already claims to cover, both confirmed by code
inspection before being added here:

- `POST`'s TRC-01 only logs `request_ids` *after* the batch is already
  valid — a rejected batch (`BatchInvalid`, 400) logs nothing, so its
  `request_id`s are unrecoverable from logs.
- `publisher/processing.py::process_item`'s **success** path — the
  overwhelmingly common case — logs nothing at all. Every failure branch in
  the same file logs (`_accepts`, `_requeue`, `_log_duplicate`), but the one
  path that mints the uuid and hands it off (`publish_pending`) discards it
  the moment it's computed (`packages/shared/src/shared/reimbursement/use_cases/publish_pending.py:60`).
  This means the single correlation this whole feature exists to guarantee —
  `request_id` → `uuid` — currently has **no durable record anywhere** for
  a successful request. TRC-12/TRC-13 below close both gaps.

## Goals

- [ ] Every write-path api route (`POST`, `PUT`) logs its correlation id
      (`request_id` pre-uuid, `uuid` post-uuid) and outcome, matching the
      rigor `publisher`/`reimbursement`'s consumers already have.
- [ ] Every read-path api route (`GET` list, `GET` by uuid) logs a baseline
      request/outcome line.
- [ ] Every agent decision node's existing log line carries the
      reimbursement uuid, so one node's behavior for one reimbursement is
      grep-able without cross-referencing the orchestrator.
- [ ] The LangFuse trace for a reimbursement's decision is queryable by that
      reimbursement's uuid.
- [ ] `publisher`'s successful insert+publish path logs the `request_id` →
      `uuid` mapping — the one correlation pair the feature is named for —
      not just its failure branches.
- [ ] A rejected `POST` batch (`BatchInvalid`) logs the `request_id`s it was
      given, not just an accepted one.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Central logging configuration (unifying the 3 independent `logging.basicConfig` calls, adding one to `api/main.py`) | A separate concern from the JSON shape of individual event log lines; not part of "the main steps" the user confirmed as in scope. |
| Deleting or reducing existing test coverage of already-shipped logging (`publisher`'s three-tier tests, `validation.py`'s uuid-logging tests) | Recommended against and not applied — see Assumptions row on this; these tests protect code this feature doesn't touch and exist to satisfy the same hard requirement this feature extends. |
| Dedicated automated tests for the logging/correlation-id behavior this feature adds (TRC-01..10, and TRC-12/13 under this amendment — same nature of change, same rationale) | Explicit user decision, prioritizing delivery speed. Verification is by code inspection plus keeping the existing full suite green. |
| Migrating `publisher/processing.py`'s and `reimbursement/validation.py`'s existing log call sites onto the new `shared.logging` helper | Already correct and already tested; minimal-impact — the helper is adopted by this feature's new call sites only. |
| New/replacement correlation-id generation (a server-generated id independent of the client-supplied `request_id`) | User chose to reuse the existing `request_id` field as-is. |
| `structlog` or any new structured-logging library | Explicitly declined in favor of a small in-house helper. |
| Logging inside `shared`'s use-case modules (`publish_pending`, `send_human_review`, `apply_decision`, `get_reimbursement`, `list_reimbursements`, `review_reimbursement`) | User call: this is one layer too deep. Orchestrator-boundary logging (api routes, `publisher`/`reimbursement`'s consumers) already covers these; `shared` stays log-free, consistent with today. **Still holds under TRC-12**: `publish_pending` gains a return-value change (the `uuid` it already computes), zero new log calls — the orchestrator (`publisher/processing.py`) is what logs it. |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Pre-uuid correlation identifier | Reuse the existing client-supplied `request_id` field (`shared.models.ReimbursementRequest.request_id`) everywhere pre-uuid logging happens | Already present, already used correctly by `publisher`'s three-tier pattern; adding a second, server-generated id would duplicate a solved problem | y |
| LangFuse uuid attachment mechanism | `metadata={"langfuse_session_id": str(reimbursement.uuid)}` passed into `graph.ainvoke`'s `config`, alongside the existing `callbacks` | Verified via Context7 against the installed `langfuse>=4.14.3` SDK: `CallbackHandler` maps `metadata["langfuse_session_id"]` to the trace's `session_id`, grouping every trace for one reimbursement (incl. retries) under one Session, filterable by uuid | y |
| Agent node logging | All 5 nodes (`validate`, `extract_fields`, `apply_policies`, `analysis`, `apply_agent_decision`) append `state["reimbursement"].uuid` to their existing log lines | User: each node is a meaningful decision step, not mechanical CRUD like `shared` | y |
| api route logging breadth | All 4 routes (`POST`, `GET` list, `GET` by uuid, `PUT`) get logging, not just the mutating two | User: "if at any point we need to verify what's going on, it must be logged," applied literally | y |
| `shared.logging` helper adoption scope | New helper is used only by this feature's new call sites in `api` (TRC-01, 02, 05, 06); `publisher`/`validation.py`'s existing, already-tested call sites are **not** migrated | Not explicitly asked — inferred from "Minimal Impact": touching already-correct, already-tested code for a pure cosmetic-shape change carries risk with no behavior benefit | n |
| Testing for this feature's own changes | No new dedicated tests for TRC-01..10; the existing full suite must stay green as the gate | Explicit user decision: "let's drop the testing of this... need speed to deliver this project" | y |
| Existing logging test coverage (`publisher`, `validation.py`) | **Not** deleted or deprioritized, despite the user's stated preference to drop it too | Flagged back to the user once: those tests protect code this feature doesn't touch and exist to hold up the same `CLAUDE.md` hard requirement this feature extends; deleting them is a separate, higher-risk action than not writing new ones. Proceeding without deletion unless the user explicitly reaffirms it here. | n |
| `PUT` decision-write failure gets a durable record (TRC-03) | A try/except around `approve_reimbursement`/`reject_reimbursement` writes a `shared.failure_log` (CRITICAL) record with `uuid`+`approved_by`+sanitized error, then re-raises | Not explicitly asked — `PUT` is the one write path with zero logging of any kind today, and a silently-lost decision-write failure is exactly what `CLAUDE.md`'s "no silent fallbacks" rule forbids. Mirrors the existing `DECISION_FAILED_EVENT` pattern in `reimbursement/validation.py` rather than inventing a new shape. | n |
| Catch-all handler enrichment (TRC-04) | `_unhandled_exception_handler` reads `request.path_params.get("uuid")` generically, covering both `GET`-by-uuid and `PUT` with one small change | Not explicitly asked — cheapest way to close the "500 on a uuid-scoped route logs no uuid" gap without new per-route code | n |
| PII handling for the actor field | `approved_by` (an email) is intentionally logged — required by `CLAUDE.md`'s own "attribute every action... the human reviewer's identity" clause. Payload bodies / OCR text remain excluded, per the existing "never log body" convention this feature doesn't change. | Directly derived from an already-established project rule; low-risk to assume | y |
| TRC-13's rejected-batch log level | INFO, via `log_event`, same tier as TRC-01's accepted-batch log | Consistency: every TRC-01..11 api log line is INFO; introducing a WARNING tier for one event alone would be a new severity precedent this feature doesn't otherwise establish. The event name (`reimbursement.batch_rejected`) itself carries the distinction — filtering by event, not level, is how this codebase already tells events apart (`publisher.processing`'s own mix of INFO/ERROR events uses level for system-vs-client severity, not routine-vs-notable) | n |
| TRC-13's `request_id` extraction on a rejected batch | Best-effort: `json.loads(raw)`, then `item.get("request_id") for item in list if isinstance(item, dict)`; empty list if `raw` isn't even a JSON array | Reuses the exact lenient-extraction shape `publisher.processing._request_id()`/`_malformed_message_record()` already use for the same "body might not even be structured" problem — no new pattern | n |

**Open questions:** none — all resolved above, either through direct answers
or as logged assumptions with rationale. Two rows above (helper adoption
scope, existing-test preservation, `PUT` failure-log, catch-all enrichment)
are agent defaults the user has not explicitly signed off on — flagged
plainly for override before or during Design.

---

## User Stories

### P1: Write-path api logging ⭐ MVP

**User Story**: As an on-call engineer, I want `POST` and `PUT` to log their
correlation id and outcome, so I can reconstruct what happened to a
request/reimbursement without re-running anything.

**Why P1**: These are the two api operations that create or change a
reimbursement's state — exactly what `CLAUDE.md`'s traceability requirement
is about, and currently the least-covered part of the whole system (`PUT`
has zero logging of any kind today).

**Acceptance Criteria**:

1. WHEN `POST /api/v1/reimbursement` successfully publishes an accepted
   batch THEN api SHALL log, at INFO via the new `shared.logging` helper, an
   event containing the batch's `request_ids` and `accepted_count` — never
   the raw request body.
2. WHEN `PUT /api/v1/reimbursement/{uuid}` commits an approve or reject
   decision THEN api SHALL log, at INFO via the `shared.logging` helper, an
   event containing the `uuid`, the resulting `status`, and the
   `approved_by` actor.
3. WHEN `PUT /api/v1/reimbursement/{uuid}`'s `approve_reimbursement`/
   `reject_reimbursement` call raises any exception THEN api SHALL write a
   durable `shared.failure_log` record (CRITICAL) containing the `uuid`, the
   `approved_by` actor, and the sanitized error, before re-raising for the
   existing app-wide handler to return 500.

**Independent Test**: Call `POST` with a batch, then `PUT` an approval —
inspect stdout/log capture for both events; confirm the fields listed above
are present.

---

### P2: Read-path api logging

**User Story**: As an on-call engineer, I want `GET` requests logged too, so
I can see who queried what when investigating an incident.

**Why P2**: Read paths don't change state, so they carry lower audit
priority than P1, but the user confirmed they're in scope.

**Acceptance Criteria**:

1. WHEN `GET /api/v1/reimbursement` (list) returns THEN api SHALL log, at
   INFO via the `shared.logging` helper, the applied status filter (or
   `"none"` if unset) and the result count.
2. WHEN `GET /api/v1/reimbursement/{uuid}` returns (200 or its own 404)
   THEN api SHALL log, at INFO via the `shared.logging` helper, the `uuid`
   and whether the row was found.

**Independent Test**: Call `GET` (list, with and without a status filter)
and `GET /{uuid}` (existing and unknown uuid) — inspect the log line for
each of the four cases.

---

### P1: Catch-all exception logging includes the uuid ⭐ MVP

**User Story**: As an on-call engineer investigating a 500, I want the
uuid included in the generic error log line whenever the failing route
operates on one, so a stack trace with no other context is still
correlatable.

**Why P1**: Currently `_unhandled_exception_handler` logs only method+path —
even for `PUT`, which has the uuid right in its path.

**Acceptance Criteria**:

1. WHEN any exception reaches `_unhandled_exception_handler` on a route
   whose path includes a `uuid` path parameter (`GET /{uuid}`, `PUT
   /{uuid}`) THEN the log line SHALL include that `uuid`, in addition to
   method+path.

**Independent Test**: Force an unhandled exception on `PUT` (e.g. a
DB-layer fault via a test double) — confirm the log line contains the path
uuid.

---

### P1: Agent node uuid logging ⭐ MVP

**User Story**: As an SRE debugging the decision pipeline, I want every
agent node's existing log line to carry the reimbursement uuid, so I can
isolate one reimbursement's full node-by-node trace with a single grep.

**Why P1**: This is the direct parallel to the user's "after Reimbursement
creation, any steps, in any layer, must have the uuid" rule, applied to the
one place inside the agent where it's currently missing despite being
in scope (`state["reimbursement"].uuid`).

**Acceptance Criteria**:

1. WHEN any of the 5 agent decision nodes (`validate`, `extract_fields`,
   `apply_policies`, `analysis`, `apply_agent_decision`) emits one of its
   existing log lines THEN that log line SHALL include
   `state["reimbursement"].uuid`.

**Independent Test**: Run the decision graph for one reimbursement, capture
logs, confirm every node's log line includes the same uuid as
`validation.py`'s orchestrator-level `DECIDED_EVENT` line for that
invocation.

---

### P1: LangFuse trace correlation ⭐ MVP

**User Story**: As an engineer investigating a decision, I want the
LangFuse trace queryable by the reimbursement's uuid, so I can jump from a
DB row straight to its full LLM trace.

**Why P1**: This is the specific, explicit ask — "LangFuse must receive
somehow the uuid" — and today it receives nothing.

**Acceptance Criteria**:

1. WHEN `agent.decide()` invokes the LangGraph decision graph THEN the
   `config` passed to `graph.ainvoke` SHALL include
   `metadata={"langfuse_session_id": str(reimbursement.uuid)}` alongside
   the existing `callbacks`.
2. WHEN the LangFuse `CallbackHandler` cannot be constructed (the existing
   fallback path in `_langfuse_handlers()`) THEN the existing
   `failure_log` fallback behavior SHALL remain unchanged — adding the
   `session_id` metadata SHALL NOT alter or bypass that fallback.

**Independent Test**: Inspect the `config` dict built by `agent.decide()`
for a given reimbursement; confirm `metadata["langfuse_session_id"] ==
str(reimbursement.uuid)`. Separately, force the existing
"LangFuse unavailable" path and confirm its behavior is byte-identical to
before this feature.

---

### P1: api's own INFO-level logs must actually emit ⭐ MVP

**User Story**: As an on-call engineer, I want the INFO-level log lines this
feature adds to `api` to actually appear somewhere, not silently vanish.

**Why P1**: Verified via Context7 against uvicorn's own default logging
setup: it configures only its own `uvicorn`/`uvicorn.error`/`uvicorn.access`
loggers, never the root logger. `api/main.py` has no
`logging.basicConfig()` call today — its one existing `logger.error(...)`
line only "works" because Python's WARNING-level `logging.lastResort`
fallback happens to cover ERROR. Every new `.info()` line TRC-01/02/05/06
add would be silently dropped without this fix.

**Acceptance Criteria**:

1. WHEN `api`'s FastAPI process starts THEN it SHALL have called
   `logging.basicConfig(level=logging.INFO)` (or equivalent), mirroring the
   pattern `publisher/consumer.py:142` and `reimbursement/consumer.py:100`
   already use on their own entrypoints — not a shared/unified config
   module (still out of scope), just parity so `api`'s own new logs emit.

**Independent Test**: Start the api process for real (or via TestClient
without capturing at the logger level, i.e. observing actual stdout/stderr)
and confirm an INFO-level line prints.

---

### P2: Shared JSON log-event helper

**User Story**: As a maintainer, I want one small shared helper defining
the JSON log-event envelope, so this feature's new log lines share one
shape instead of each hand-rolling `json.dumps(...)`.

**Why P2**: Supports P1's stories but isn't itself user-facing; scoped down
per the Out of Scope table to new call sites only.

**Acceptance Criteria**:

1. WHEN any of this feature's new api log lines (P1's `POST`/`PUT`
   stories, P2's `GET` stories) is emitted THEN it SHALL be built via a new
   `shared.logging.log_event(logger, level, event, **fields)` helper that
   emits one JSON object (`{"event": ..., **fields}`) per call.

**Independent Test**: Code inspection — every new call site added by this
feature calls `log_event`, none hand-rolls its own `json.dumps(...)`.

---

### P1: Publisher success-path correlation logging ⭐ MVP

**User Story**: As an on-call engineer, I want `publisher` to log the
`request_id` → `uuid` mapping when it successfully inserts and publishes an
item, so a client-reported `request_id` is greppable directly to the
`uuid` it became — without querying the database.

**Why P1**: This is the actual correlation the feature is named for, on
its most common path. Every failure branch in `publisher/processing.py`
already logs; the success branch (`ItemOutcome.PUBLISHED`) is the one path
that currently logs nothing at all, despite being where the uuid is minted.

**Acceptance Criteria**:

1. WHEN `process_item` successfully inserts and publishes an item THEN
   publisher SHALL log, at INFO via the `shared.logging` helper, an event
   containing the item's `request_id` and the resulting `uuid`, before
   returning `ItemOutcome.PUBLISHED`.
2. `publish_pending` (the `shared` use case) continues to add no log calls
   of its own — it returns the `uuid` it already computes, so the
   orchestrator (`publisher/processing.py`) is what logs it, consistent
   with the Out of Scope table's shared-stays-log-free rule.

**Independent Test**: Publish a valid batch through `publisher`'s consumer,
inspect stdout/log capture for the new event, confirm `request_id`/`uuid`
are both present and the uuid matches the row `publisher` inserted.

---

### P1: API reject-path request-id logging ⭐ MVP

**User Story**: As an on-call engineer, I want a rejected `POST` batch's
`request_id`s logged before the 400 response, so a client dispute about
"I sent X" is verifiable even for a batch that never got past validation.

**Why P1**: TRC-01 only logs on the accept path; a rejected batch — the one
case where the client and the system are most likely to disagree about
what was sent — currently logs nothing.

**Acceptance Criteria**:

1. WHEN `POST /api/v1/reimbursement`'s body fails `validate_batch`
   (`BatchInvalid`) THEN api SHALL log, at INFO via the `shared.logging`
   helper, an event containing the best-effort extracted `request_id`s (see
   Assumptions row above) and the rejection reason, before re-raising for
   the existing `_batch_invalid_handler` to return its unchanged 400.
2. The raw request body SHALL NOT appear in the log line — only the
   extracted `request_id` strings and the rejection message.

**Independent Test**: `POST` a batch with one item missing `request_id` and
one with `receipts_currency` invalid — inspect the log line, confirm every
extractable `request_id` from the batch is present and the raw body is not.

---

## Edge Cases

- WHEN `PUT`'s body fails its own shape validation (422, before reaching
  the use case) THEN no `approved_by`/`uuid` decision-outcome log fires —
  only the existing 422 handler's response; TRC-02/TRC-03 apply only past
  that point, once an actor identity exists to attribute.
- WHEN `GET` (list) is called with no `status` filter THEN the logged
  filter value SHALL be the literal string `"none"`, not an empty string or
  `null` — one interpretation only.
- WHEN a reimbursement's decision is retried (the graph re-invoked for the
  same uuid) THEN every re-invocation's LangFuse trace SHALL carry the same
  `langfuse_session_id`, since the uuid is stable across retries — all
  attempts for one reimbursement group under one Session.
- WHEN a rejected `POST` batch's raw body isn't even a JSON array of objects
  (e.g. malformed JSON, or a JSON object instead of a list) THEN TRC-13's
  logged `request_id`s list SHALL be empty rather than raising a second
  exception out of the logging path itself.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| TRC-01 | P1: Write-path api logging | Verified | Merged (PR #12) |
| TRC-02 | P1: Write-path api logging | Verified | Merged (PR #12) |
| TRC-03 | P1: Write-path api logging | Verified | Merged (PR #12) |
| TRC-04 | P1: Catch-all exception logging | Verified | Merged (PR #12) |
| TRC-05 | P2: Read-path api logging | Verified | Merged (PR #12) |
| TRC-06 | P2: Read-path api logging | Verified | Merged (PR #12) |
| TRC-07 | P1: Agent node uuid logging | Verified | Merged (PR #12) |
| TRC-08 | P1: LangFuse trace correlation | Verified | Merged (PR #12) |
| TRC-09 | P1: LangFuse trace correlation | Verified | Merged (PR #12) |
| TRC-10 | P2: Shared JSON log-event helper | Verified | Merged (PR #12) |
| TRC-11 | P1: api's own INFO-level logs must actually emit | Verified | Merged (PR #12) |
| TRC-12 | P1: Publisher success-path correlation logging | Implemented | Gate green (T10) |
| TRC-13 | P1: API reject-path request-id logging | Implemented | Gate green (T11) |

**Coverage:** 13 total, 13 implemented and gate-green; TRC-12/13 pending an
independent Verifier pass before merge (same bar TRC-01..11 cleared).

---

## Success Criteria

- [x] Every one of the 4 api routes emits at least one log line per
      invocation carrying `request_id` (POST) or `uuid` (GET/GET-by-uuid/PUT).
- [x] All 5 agent decision nodes' existing log lines carry the reimbursement
      uuid.
- [x] Every `graph.ainvoke` call's `config` includes
      `metadata={"langfuse_session_id": str(uuid)}`.
- [x] Given a reimbursement uuid, its full lifecycle (api write → publisher
      → agent nodes → LangFuse trace) is reconstructable by grep + the
      LangFuse UI's session filter, without re-running anything and without
      falling back to a direct DB query for the `request_id` → `uuid` step.
- [x] The existing full test suite remains green (no new tests added for
      this feature, per the logged assumption above).
