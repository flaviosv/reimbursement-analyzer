# POST /api/v1/reimbursement — Ingress & Kafka Publish Specification

## Problem Statement

`docs/SCOPE.md:293` makes no-request-loss the governing constraint: the system
is mission-critical and financial, so the HTTP edge must durably hand every
accepted request to Kafka before acknowledging the client. Today the API
exposes only `GET /health` (`src/api/src/api/main.py:7-9`) — there is no
ingress at all, no validation layer, and no producer. Every downstream
component (publisher, agent, human review) is starved of input.

This feature delivers the ingress edge only: validate, size-limit, publish,
answer.

## Goals

- [ ] `POST /api/v1/reimbursement` accepts a batch of reimbursement requests,
      validates the minimum contract from `docs/SCOPE.md:126-133` with
      Pydantic, and publishes the batch to the `Request` topic.
- [ ] A `201` is returned **only** after the broker has acknowledged the
      message; any publish failure surfaces as `500` and nothing is silently
      accepted (`SCOPE.md:120-121`).
- [ ] The documented 25 MB payload ceiling is real end-to-end — enforced at the
      HTTP edge as `413`, and provably deliverable through the broker.
- [ ] Every response body matches the `{"msg": ""}` contract
      (`SCOPE.md:140-144`), including validation failures, which FastAPI would
      otherwise answer as `422` with a `detail[]` array.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| `GET /api/v1/reimbursement` | Separate endpoint (`SCOPE.md:146-173`); user scoped this feature to POST only |
| `PUT /api/v1/reimbursement/:uuid` | Separate endpoint (`SCOPE.md:175-204`) |
| Any database access, schema, or migration | Owned by `.specs/features/db-schema-migrations`; the API never touches PostgreSQL |
| Publisher consumption of the `Request` topic | `SCOPE.md:206-221` — a separate service and feature |
| Agent decision logic (90-day, 200, 2000 rules) | `SCOPE.md:223-274`; the API makes no decisions and applies no approval policy |
| UUID assignment to a reimbursement | Assigned by the publisher on DB insert (`SCOPE.md:210`), not by the API |
| Duplicate / idempotency detection | Enforced by the DB unique constraint downstream (`SCOPE.md:91`); the API is deliberately dumb |
| Authentication / authorization | Deliberate project decision (`SCOPE.md:295-297`) |
| Explicit Kafka topic pre-creation at bootstrap | `SCOPE.md:54` bootstrap concern; broker-level sizing covers auto-created topics — see Assumptions |
| Consumer-side fetch sizing (`fetch.max.bytes`) | Belongs to the publisher/agent features that actually consume 25 MB messages — flagged as a downstream dependency below |
| Rate limiting / throttling | No requirement in `SCOPE.md`; no auth exists to key a limit on |
| Structured-logging framework | `CONCERNS.md` tracks it project-wide; this feature uses the existing stdout convention |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Request body shape | JSON **array** of request objects | `SCOPE.md:210` has the publisher "iterate over each item from the message", and `docs/original/sample.json` is a 3-item array. `SCOPE.md:127-133` showing one object is read as an illustration of one item's fields | y |
| Batch failure semantics | All-or-nothing: one invalid item rejects the whole batch with `400`, nothing is published | One batch is one Kafka message, so "accepted" and "published" stay the same fact. Partial success would need a `207`, which is not in the documented status set (`SCOPE.md:135-138`) | y |
| Published message shape | Envelope `{"retry": 0, "published_at": ..., "payload": <body verbatim>}` | `SCOPE.md:120` requires `retry = 0` on the message and `SCOPE.md:122` requires "the entire payload". Byte-verbatim payload preserves key order, numeric literals, and unicode escaping for the audit trail (`SCOPE.md:21`) | y |
| Meaning of `retry = 0` (`SCOPE.md:120`) | The envelope's retry **counter**, not librdkafka's `retries` producer property | `ARCHITECTURE.md:83` defines "a `retry` counter carried in the message"; the publisher increments it on failure (`SCOPE.md:221`). librdkafka `retries` keeps its default — disabling client retries would *increase* request loss, contradicting `SCOPE.md:293` | y |
| Validation strictness | `request_id` non-empty `str`; `submitted_by` `EmailStr`; `submitted_at` `AwareDatetime`; `extra="allow"` | "Accept any payload" (`SCOPE.md:124`) with exactly three required fields (`SCOPE.md:126-133`, `SCOPE.md:288-292`). A naive datetime silently loses the offset; the DB layer already treats `submitted_by` as an email (`SCOPE.md:94`) | y |
| Kafka message-size configuration | In scope — broker and producer both raised | Without it the `413`/25 MB contract is unenforceable: librdkafka `message.max.bytes` defaults to **1 000 000** and the broker default is ~1 MB, so a valid 25 MB request would 500 on publish. `SCOPE.md:344` explicitly requires this | y |
| 25 MB enforcement mechanism | Count bytes while streaming the body; abort past the ceiling | `Content-Length` is absent under chunked transfer-encoding and is client-controlled. Streaming also avoids buffering an oversized body before rejecting it | y |
| Delivery acknowledgement | Block on the per-message delivery report before responding | `SCOPE.md:121` — "If fails, reject completely the payload" is only implementable if the response waits for the broker verdict | y |
| `413` byte ceiling | `26_214_400` bytes (25 MiB) | "25mb" (`SCOPE.md:125`) read as MiB, the conventional binary reading for a byte limit | **n** |
| Kafka size ceiling | `27_262_976` bytes (26 MiB) at broker, topic default, and producer | The envelope wraps the body, so a 25 MiB body yields a >25 MiB message; protocol framing adds more. 1 MiB of headroom is the smallest round number that cannot be hit by envelope overhead | **n** |
| Kafka topic name | `Request`, verbatim | `SCOPE.md:120` names the topic `Request`. Valid Kafka topic characters; renaming would desynchronise this spec from the publisher's spec | y |
| Topic creation | Rely on broker auto-create, inheriting the raised broker-level `message.max.bytes` | Auto-created topics take the broker default, so no explicit `max.message.bytes` override is needed. Explicit creation is `SCOPE.md:54`'s bootstrap concern, out of this feature | **n** |
| Message key | `None` (no key) | A batch has N `request_id`s, so no single natural key exists. Round-robin partitioning is correct for a batch message | y |
| Empty array `[]` | Rejected with `400` | Publishing an empty batch produces a message the publisher would iterate zero times — a no-op that consumes a partition offset and pollutes the audit trail | **n** |
| Top-level JSON object instead of an array | Rejected with `400` | The contract is a batch; silently accepting both shapes creates two contracts to version and test (explicitly declined during discussion) | y |
| Error-message content | Item index + field name + reason; **never** the offending value | A payload may carry PII (`SCOPE.md:59`) and can be 25 MB. Echoing values into responses and logs leaks both | y |
| `201` response body | `{"msg": "<n> request(s) accepted"}` | `SCOPE.md:139-144` defines a single `{"msg": ""}` body for the endpoint. No UUID is available to return — the publisher assigns it (`SCOPE.md:210`) | **n** |
| Producer lifecycle | One `Producer` created in the FastAPI lifespan, shared across requests | librdkafka's `Producer` is thread-safe and batches internally; per-request construction would rebuild broker metadata on every call | y |
| Producer durability config | `acks=all`, `enable.idempotence=true` | `SCOPE.md:293` — no request may be lost. `acks=1` acknowledges before replication; idempotence prevents duplicates from client-side retries | y |
| Blocking-call handling | `produce`/`poll` run off the event loop (`run_in_threadpool`) | `confluent_kafka` is a blocking C extension; calling it directly in an `async def` route stalls every other request on the event loop | y |
| Where the Pydantic models live | `ReimbursementRequest` + `RequestEnvelope` in `src/shared/src/shared/models.py`; `MessageResponse` in `src/api/src/api/responses.py` | `ARCHITECTURE.md:51` and `STRUCTURE.md:46` designate `shared` as the home for **cross-service** Pydantic models, and the publisher must parse the same envelope. `MessageResponse` fails that test — no other service builds HTTP response bodies — so it stays in the api layer (user correction, 2026-08-07) | y |
| New dependency | `email-validator` (via `pydantic[email]`) on `shared` | `EmailStr` raises at import time without it (verified against Pydantic docs via Context7, 2026-08-07) | y |
| Test framework | pytest + httpx ASGI transport for unit tests; a **testcontainers `KafkaContainer`** for the integration test, plus a parity test over `docker-compose.yml` | Conforms to **AD-008**, which superseded AD-006 and made hermetic containers the project pattern. AD-006's objection — that a throwaway broker verifies a config we do not ship — is answered by splitting `RCV-20`: the container proves 25 MiB flows, and `test_compose_parity.py` proves the shipped compose file carries that same number. Both halves are stronger than the single compose-broker test that replaced the spec's original testcontainers choice earlier on 2026-08-07 | y |
| Test infrastructure ownership | Already exists — pytest in root `[dependency-groups] dev`, `testpaths = ["src/api/tests"]` | Established by the `db-schema-migrations` feature after this spec was first written. This feature adds test *files*, not test *infrastructure* | y |
| Locale / currency / date format at the edge (`SCOPE.md:28`) | Not interpreted by this endpoint | The API is a transport edge: it validates identity fields and forwards bytes. Currency (`BRL`) and receipt-date parsing belong to the agent (`SCOPE.md:245`, `SCOPE.md:255-258`) | y |

**Open questions:** none — the five rows marked **n** are recorded assumptions
with a chosen default and rationale. Each is independently reversible (a
constant, a compose value, or one branch) and none reshapes the spec.

**Downstream dependency flagged, not fixed here:** raising broker
`message.max.bytes` lets 25 MB messages be *written*. The publisher's consumer
will additionally need `fetch.max.bytes` / `max.partition.fetch.bytes` raised
to *read* them. That belongs to the publisher feature; recorded here so it is
not discovered in production.

---

## User Stories

### P1: Batch of reimbursement requests accepted and published ⭐ MVP

**User Story**: As a client system, I want to POST a batch of reimbursement
requests and receive a `201` only once Kafka has durably accepted them, so
that a successful response is a guarantee the request will be processed.

**Why P1**: This is the system's only entry point. Without it, no request ever
enters the pipeline.

**Acceptance Criteria**:

1. WHEN a JSON array of valid request objects is POSTed to
   `/api/v1/reimbursement` AND the broker acknowledges the message THEN the
   system SHALL respond `201` with body `{"msg": "<n> request(s) accepted"}`.
2. WHEN a valid batch is accepted THEN the system SHALL publish exactly **one**
   message to the topic `Request`.
3. WHEN the message is published THEN its value SHALL be a JSON object whose
   `retry` member is the integer `0`.
4. WHEN the message is published THEN its `payload` member SHALL be the request
   body **byte-for-byte identical** to what the client sent, including key
   order, numeric literal formatting, and unicode escaping.
5. WHEN the message is published THEN its `published_at` member SHALL be an
   RFC 3339 UTC timestamp recorded at publish time.
6. WHEN a request object carries fields beyond the three required ones (e.g.
   `raw_ocr_text`, `claimed_amount_brl`, `attachments`) THEN the system SHALL
   accept them and forward them unaltered.

**Independent Test**: POST `docs/original/sample.json` verbatim against the
running API with a broker attached; assert `201`, consume the `Request` topic,
and assert the consumed `payload` bytes equal the file's bytes.

---

### P1: Minimum-contract validation via Pydantic ⭐ MVP

**User Story**: As an operator, I want structurally unusable requests rejected
at the edge, so that malformed data never enters the pipeline and never
consumes agent or LLM budget.

**Why P1**: `SCOPE.md:288-292` states these three fields are required "for the
sake of requester's identity". A request without them is untraceable, which
directly violates the audit-trail requirement (`SCOPE.md:15`).

**Acceptance Criteria**:

1. WHEN any item in the batch omits `request_id`, `submitted_by`, or
   `submitted_at` THEN the system SHALL respond `400` and publish nothing.
2. WHEN any item's `request_id` is absent, not a string, or an empty/whitespace
   string THEN the system SHALL respond `400` and publish nothing.
3. WHEN any item's `submitted_by` is not a syntactically valid email address
   THEN the system SHALL respond `400` and publish nothing.
4. WHEN any item's `submitted_at` is not a parseable ISO-8601 datetime, or is
   parseable but carries **no** timezone offset THEN the system SHALL respond
   `400` and publish nothing.
5. WHEN item *i* of the batch fails validation THEN the response `msg` SHALL
   name the zero-based index *i* and the offending field.
6. WHEN a validation failure is reported THEN the response `msg` SHALL NOT
   contain the offending field's value.
7. WHEN the body is not valid JSON THEN the system SHALL respond `400` and
   publish nothing.
8. WHEN the body is valid JSON but not an array (an object, string, or number)
   THEN the system SHALL respond `400` and publish nothing.
9. WHEN the body is an empty array `[]` THEN the system SHALL respond `400` and
   publish nothing.
10. WHEN any validation failure occurs THEN the response body SHALL be exactly
    `{"msg": "..."}` — never FastAPI's default `422` status or `detail[]` array.

**Independent Test**: Drive a table of malformed bodies through the ASGI app
with a fake producer; assert `400`, the `{"msg": ...}` shape, and that the
producer recorded zero `produce()` calls.

---

### P1: Publish failure is never silently accepted ⭐ MVP

**User Story**: As the system owner, I want a request rejected outright when
Kafka does not confirm it, so that a `201` never covers a lost request.

**Why P1**: `SCOPE.md:121` — "If fails, reject completely the payload".
`SCOPE.md:293` makes no-loss the reason the whole event-driven design exists. A
fire-and-forget `201` would defeat both.

**Acceptance Criteria**:

1. WHEN a valid batch is submitted THEN the system SHALL NOT respond `201`
   before the broker's delivery report for that message has been received.
2. WHEN the broker returns a delivery error for the message THEN the system
   SHALL respond `500` with body `{"msg": "..."}`.
3. WHEN no delivery report arrives within the configured publish timeout THEN
   the system SHALL respond `500` with body `{"msg": "..."}`.
4. WHEN the broker is unreachable at request time THEN the system SHALL respond
   `500` — never `201`, and never hang past the publish timeout.
5. WHEN a publish fails THEN the system SHALL log the failure with the batch's
   `request_id` values and the broker error, and SHALL NOT log the payload body.
6. WHEN two requests are published concurrently THEN each response SHALL be
   determined by **its own** message's delivery report, never by another
   in-flight request's outcome.

**Independent Test**: Inject a fake producer whose delivery callback reports an
error; assert `500`. Repeat with a callback that never fires; assert `500` at
the timeout boundary, not a hang. For AC 6, produce two concurrent requests
where only one delivery report fails and assert the verdicts do not cross.

---

### P1: 25 MB ceiling enforced end to end ⭐ MVP

**User Story**: As an operator, I want the documented 25 MB limit enforced at
the edge and honoured by the broker, so that the endpoint's stated contract is
the contract it actually delivers.

**Why P1**: `SCOPE.md:125` documents the ceiling and `SCOPE.md:137` documents
the `413`. `SCOPE.md:344` explicitly requires Kafka be able to carry it.
Enforcing only one half ships a knowingly broken contract.

**Acceptance Criteria**:

1. WHEN the request body exceeds 26 214 400 bytes THEN the system SHALL respond
   `413` with body `{"msg": "..."}` and publish nothing.
2. WHEN the body exceeds the ceiling THEN the system SHALL stop reading and
   respond without buffering the complete body in memory.
3. WHEN the request declares no `Content-Length` (chunked transfer-encoding)
   and streams more than the ceiling THEN the system SHALL still respond `413`.
4. WHEN the request declares a `Content-Length` **smaller** than the bytes it
   actually streams, and the streamed total exceeds the ceiling THEN the system
   SHALL still respond `413`.
5. WHEN a body of exactly 26 214 400 bytes is submitted THEN the system SHALL
   NOT respond `413` (the ceiling is inclusive).
6. WHEN a valid body at or near the ceiling is published THEN the broker SHALL
   accept the resulting message — broker, topic-default, and producer size
   limits SHALL all be configured above the ceiling plus envelope overhead.

**Independent Test**: Stream a 26 214 401-byte body, assert `413`; stream a
26 214 400-byte body, assert it is not a `413`. Against a `KafkaContainer`
sized from `config.KAFKA_MAX_MESSAGE_BYTES`, publish a payload at the ceiling
and consume it back intact. Separately, parse `docker-compose.yml` and assert
its `KAFKA_MESSAGE_MAX_BYTES` equals that same constant.

---

### P2: Endpoint documented in OpenAPI

**User Story**: As an API consumer, I want the endpoint, its request shape, and
all four response codes visible in Swagger, so that I can integrate without
reading the source.

**Why P2**: `SCOPE.md:282-284` requires Swagger support and FastAPI supplies it
for free once the route carries explicit response models — but the four
documented status codes will be missing from the schema unless declared.

**Acceptance Criteria**:

1. WHEN the OpenAPI schema is fetched THEN it SHALL describe
   `POST /api/v1/reimbursement` with an array request body.
2. WHEN the OpenAPI schema is fetched THEN it SHALL declare responses `201`,
   `400`, `413`, and `500`, each with the `{"msg": "..."}` body model.

**Independent Test**: `GET /openapi.json` and assert the path, the array body
schema, and all four documented status codes.

---

## Edge Cases

- WHEN the body is `[]` THEN the system SHALL respond `400` — an empty batch is
  a no-op that would still consume a partition offset.
- WHEN a duplicate `(request_id, submitted_by)` pair is submitted THEN the
  system SHALL accept and publish it — deduplication is the database's job
  (`SCOPE.md:91`), and rejecting here would make the edge stateful.
- WHEN `submitted_at` is in the future THEN the system SHALL accept it — no
  requirement bounds it, and rejecting introduces a clock-skew failure mode
  (explicitly declined during discussion).
- WHEN an item contains a `retry` or `payload` key of its own THEN the system
  SHALL forward it untouched — those names are reserved on the **envelope**,
  one level above the payload, so no collision is possible.
- WHEN the body is valid JSON but deeply nested or adversarially shaped THEN
  the system SHALL rely on the byte ceiling as the bound; no separate depth
  limit is imposed.
- WHEN two requests are published concurrently THEN a per-message delivery
  callback SHALL determine each verdict — a bare `flush()` drains the whole
  shared producer queue and would let one request's outcome decide another's.
- WHEN `Content-Type` is not `application/json` but the body parses as JSON
  THEN the system SHALL accept it — the body is validated, not the header.
- WHEN the envelope pushes a ceiling-sized body past 25 MiB THEN the broker
  SHALL still accept it, because the Kafka-side limit is set above the HTTP
  ceiling by design.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ------ | ------- |
| RCV-01 | P1: Batch accepted and published (201 after broker ack) | Implemented | ✅ Verified |
| RCV-02 | P1: Batch accepted and published (exactly one message to `Request`) | Implemented | ✅ Verified |
| RCV-03 | P1: Batch accepted and published (envelope `retry = 0`) | Implemented | ✅ Verified |
| RCV-04 | P1: Batch accepted and published (payload byte-verbatim) | Implemented | ✅ Verified |
| RCV-05 | P1: Batch accepted and published (`published_at` stamp) | Implemented | ✅ Verified |
| RCV-06 | P1: Batch accepted and published (extra fields forwarded unaltered) | Implemented | ✅ Verified |
| RCV-07 | P1: Validation (three required fields present) | Implemented | ✅ Verified |
| RCV-08 | P1: Validation (`submitted_by` is a valid email) | Implemented | ✅ Verified |
| RCV-09 | P1: Validation (`submitted_at` is timezone-aware ISO-8601) | Implemented | ✅ Verified |
| RCV-10 | P1: Validation (all-or-nothing batch rejection, indexed error) | Implemented | ✅ Verified |
| RCV-11 | P1: Validation (no field values echoed in errors or logs) | Implemented | ✅ Verified |
| RCV-12 | P1: Validation (non-JSON, non-array, and empty-array bodies rejected) | Implemented | ✅ Verified |
| RCV-13 | P1: Validation (`400` + `{"msg"}` replaces FastAPI's `422` + `detail[]`) | Implemented | ✅ Verified |
| RCV-14 | P1: Publish failure (delivery report gates the response) | Implemented | ✅ Verified |
| RCV-15 | P1: Publish failure (broker error and timeout both yield `500`) | Implemented | ✅ Verified |
| RCV-16 | P1: Publish failure (per-message verdict under concurrency) | Implemented | ✅ Verified |
| RCV-17 | P1: Publish failure (failure logged without payload body) | Implemented | ✅ Verified |
| RCV-18 | P1: 25 MB ceiling (`413` above the limit, streaming-enforced) | Implemented | ✅ Verified |
| RCV-19 | P1: 25 MB ceiling (inclusive boundary at 26 214 400 bytes) | Implemented | ✅ Verified |
| RCV-20 | P1: 25 MB ceiling (broker + producer sized above the ceiling) | Implemented | ✅ Verified |
| RCV-21 | P2: OpenAPI documents the route and all four status codes | Implemented | ✅ Verified |

**Coverage:** 21 total, 0 mapped to tasks, 21 unmapped ⚠️ (Tasks phase pending)

---

## Success Criteria

- [ ] `docs/original/sample.json` POSTed verbatim returns `201` and reappears
      byte-for-byte in the `payload` member of a single `Request`-topic message.
- [ ] Every `400` / `413` / `500` path returns exactly `{"msg": "..."}` — no
      FastAPI `422`, no `detail[]`, anywhere in the endpoint's surface.
- [ ] A 25 MiB payload round-trips through a real broker in an automated test.
- [ ] No test passes by asserting that a fake producer was called — at least one
      test proves an actual broker accepted an actual ceiling-sized message.
- [ ] A broker outage produces `500`s, never `201`s, and never a hung request.
- [ ] `GET /health` still returns `200` and no existing behaviour changes.
