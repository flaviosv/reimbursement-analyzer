# POST /api/v1/reimbursement — Context

**Gathered:** 2026-08-07
**Spec:** `.specs/features/api-post-reimbursement/spec.md`
**Status:** Ready for design

---

## Feature Boundary

`POST /api/v1/reimbursement` only: a Pydantic validation layer over the
incoming batch, a byte-size ceiling, and a durable publish to the `Request`
Kafka topic. No database, no publisher/agent logic, no `GET`, no `PUT`. The one
deliberate step outside `src/api` is Kafka message-size configuration, because
the endpoint's documented 25 MB contract is unenforceable without it.

---

## Implementation Decisions

### Request body shape

- The endpoint accepts a **JSON array** of request objects. A bare object is
  rejected with `400` — one contract, not two.
- Resolves the contradiction between `SCOPE.md:127-133` (single object) and
  `SCOPE.md:210` (publisher iterates over each item): the array is
  authoritative, and `docs/original/sample.json` confirms it.
- One HTTP request becomes exactly one Kafka message carrying N items.

### Batch failure semantics

- All-or-nothing. One invalid item rejects the entire batch with `400` and
  nothing is published.
- Partial success (`207`-style) was explicitly rejected — it is not among the
  documented status codes and would force the client to reconcile which items
  landed.
- The error message names the failing zero-based index and field, never the
  value.

### Published message shape

- Envelope: `{"retry": 0, "published_at": "<RFC 3339 UTC>", "payload": <body>}`.
- `payload` is the request body **byte-for-byte verbatim** — not a
  re-serialisation of the validated model. Key order, numeric literals, and
  unicode escaping are preserved for the audit trail.
- `retry` is the message-carried retry counter of `ARCHITECTURE.md:83`, which
  the publisher increments on failure — **not** librdkafka's `retries` producer
  property. Client-side producer retries stay at their default; disabling them
  would increase request loss.
- No message key: a batch has N `request_id`s, so no single natural key exists.

### Validation strictness

- `request_id`: non-empty string.
- `submitted_by`: `EmailStr` (pulls in `email-validator`).
- `submitted_at`: `AwareDatetime` — parseable ISO-8601 **with** a timezone
  offset; a naive datetime is rejected.
- `extra="allow"` — every other field passes through untouched, per "accept any
  payload" (`SCOPE.md:124`).
- Rejecting a future `submitted_at` was considered and declined: no requirement
  bounds it and it adds a clock-skew failure mode.
- FastAPI's default `422` + `detail[]` is replaced by `400` + `{"msg": "..."}`
  across the endpoint.

### 25 MB ceiling

- Enforced by counting bytes while streaming the body, aborting as soon as the
  running total passes the ceiling. `Content-Length` is not trusted — it is
  absent under chunked encoding and is client-controlled.
- Ceiling is `26_214_400` bytes (25 MiB), inclusive.
- Kafka is sized end to end: broker `message.max.bytes` and producer
  `message.max.bytes` raised to `27_262_976` (26 MiB), leaving headroom for the
  envelope and protocol framing. Verified: librdkafka's default is `1_000_000`
  with a valid range of `1_000`–`1_000_000_000`.

### Delivery acknowledgement

- `201` is returned only after the broker acknowledges **that** message.
- Per-message delivery callback, not a bare `flush()` — `flush()` drains the
  shared producer queue and would let one request's outcome decide another's.
- Broker error, or no report within the publish timeout, both yield `500`.
- `produce`/`poll` run off the event loop; `confluent_kafka` is a blocking C
  extension.
- Producer configured `acks=all`, `enable.idempotence=true`, created once in the
  FastAPI lifespan.

### Test gate

- pytest + httpx ASGI transport with a fake publisher for the validation
  matrix, error-body shape, and envelope contents.
- Integration test against a **testcontainers `KafkaContainer`**, publishing a
  ceiling-sized message and consuming it back. Conforms to **AD-008**, which
  superseded AD-006 and made hermetic containers the project pattern.
  - The objection this document previously raised — that a throwaway broker
    verifies a config we never deploy — is answered by splitting `RCV-20` in
    two. `test_kafka_integration.py` proves 25 MiB flows through a broker;
    `test_compose_parity.py` parses `docker-compose.yml` and asserts the number
    it ships equals `config.KAFKA_MAX_MESSAGE_BYTES`. The container is
    configured from that same constant, so all three copies are pinned to one
    source and drift fails a test.
- Test infrastructure itself (pytest, `testpaths`, `src/api/tests/`,
  `conftest.py`, testcontainers) already exists — shipped by
  `db-schema-migrations`. This feature adds test files and **extends**
  `conftest.py`; it never rewrites it.

---

### Module layout — vertical slices

- Decided by the user on 2026-08-07, overriding the earlier "agent's discretion"
  note: `src/api` is organised as **vertical slices**, one directory per
  operation. This feature creates
  `src/api/src/api/reimbursement/create/{route,validation,producer}.py`.
- Reason given: a flat module list is hard to maintain and understand, and gets
  worse with every endpoint added.
- `validation.py` absorbs the byte-cap reader — the slice is split by request
  *stage* (reject → publish → respond), not by HTTP status code.
- Three things stay at the `api` root by exception, each justified in
  `design.md`: `errors.py` (app-wide response contract), `kafka.py`
  (`AIOProducer` lifecycle, owned by the FastAPI lifespan), and `config.py`
  (only constants compared against something outside a slice).
- Tests mirror the slices. This requires `pythonpath` and
  `--import-mode=importlib` in the root `pyproject.toml`; both were verified by
  experiment on pytest 9.1.1.

---

## Agent's Discretion

- The exact publish-timeout value.
- Precise wording of the `msg` strings, subject to the no-values constraint.

---

## Declined / Undiscussed Gray Areas → Assumptions

Each is recorded in the spec's Assumptions & Open Questions table with a chosen
default and rationale:

- Exact `413` byte ceiling (MiB vs MB reading of "25mb").
- Kafka headroom figure above the HTTP ceiling.
- Topic auto-creation vs explicit pre-creation at bootstrap.
- Empty-array handling.
- Exact `201` response `msg` wording.

---

## Specific References

- `docs/original/sample.json` is the canonical happy-path body — the spec's
  primary success criterion is that this exact file round-trips byte-for-byte.
- `SCOPE.md:120-144` is the endpoint contract of record.
- `ARCHITECTURE.md:60-65` describes this exact step of the target data flow.

---

## Deferred Ideas

- **Consumer-side fetch sizing.** Raising broker `message.max.bytes` lets 25 MB
  messages be written; the publisher's consumer needs `fetch.max.bytes` /
  `max.partition.fetch.bytes` raised to read them. Belongs to the publisher
  feature — recorded so it is not discovered in production.
- **Explicit Kafka topic pre-creation at bootstrap** (`SCOPE.md:54`) — a
  bootstrap concern alongside the `migrate` service from the db-schema-migrations
  spec.
- **Move `HealthStatus` out of `shared`.** The rule this feature adopts is that
  `shared` holds only cross-service wire contracts; `MessageResponse` was moved
  to `src/api/src/api/responses.py` for exactly that reason. `HealthStatus` is
  api-only too but predates the rule, and `/health` is outside this feature's
  scope, so it stays put — a known exception, not a second precedent.
- **Structured logging framework** — tracked project-wide in `CONCERNS.md`.
- **Rate limiting** — meaningless without authentication, which is a deliberate
  project-level scope cut (`SCOPE.md:295-297`).
