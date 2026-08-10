# End-to-End Pipeline Flow & Dotenv-First Runtime Configuration Specification

## Problem Statement

Every service (`api`, `publisher`, `reimbursement`) has its own single-hop
"integration" test against real Postgres/Kafka testcontainers, but nothing
chains them together — no test proves a request actually survives
`api → publisher → reimbursement agent → persisted decision → GET/PUT` end
to end.

Separately, this project's own convention (README, every entrypoint calling
`load_dotenv()`) states `.env` is the single source of truth for
configuration — but that's only true when a service runs directly on the
host. `.dockerignore` excludes `.env`, and no service bind-mounts the repo
root, so `.env` is unreachable inside every container: `load_dotenv()` is a
silent no-op there today. `docker-compose.yml`'s per-service `environment:`
block is the *only* thing that currently delivers a value into a running
container, which has two consequences: (1) it duplicates the `.env` values
it re-lists as `${VAR}`, contradicting "rely on dotenv" as the actual
mechanism inside Docker, and (2) that duplication has already drifted —
`reimbursement`'s block is missing `GROQ_API_KEY`/model-name vars entirely
(`docker compose up reimbursement` fails fast on `_require_env`), and
`publisher`/`reimbursement` are both missing the Kafka SASL/TLS vars `api`
alone receives. Building the new e2e suite on top of that gap would inherit
the same failure.

## Goals

- [ ] `.env` is the actual runtime source of truth for app config
      **inside containers, not just on the host** — `api`, `publisher`,
      `reimbursement`, and `migrate` load it via `python-dotenv` at
      container start, and `docker-compose.yml`'s `environment:` blocks
      carry only what genuinely cannot come from a file shared between the
      host and the Docker network (network-topology addresses).
- [ ] `docker compose up -d` boots `api`, `publisher`, and `reimbursement`
      healthy from a fresh `cp .env.sample .env` alone.
- [ ] An automated, opt-in e2e suite drives a real reimbursement through the
      full real stack (`docker compose` containers, real Groq calls) and
      proves every documented outcome branch — auto-approve, auto-reject,
      human-review + PUT resolution, retry-ceiling escalation, ghost/stale
      handling — actually lands correctly when the three services talk to
      each other for real, not just individually.
- [ ] The full-traceability requirement (`CLAUDE.md`) is provable
      end-to-end: a LangFuse trace is queryable by the reimbursement's own
      uuid after a real decision.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature                                                                 | Reason                                                                                                                                                     |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Assembling `DATABASE_URL`/Kafka bootstrap servers from discrete dotenv-sourced parts (host, port, user, password) instead of one compose-constructed connection string | Would require restructuring `shared.config.DatabaseConfig`/`KafkaConfig` to build connection strings from parts plus one topology-only host override — a code-architecture change, not a `docker-compose.yml` routing fix. The hostname component (`postgres`/`kafka` vs. a host-machine address) is inherently different between "running in the Docker network" and "running on the host" and cannot be a single shared `.env` value either way; the DSN/bootstrap-servers strings stay compose-constructed for that reason. |
| Converting the existing fake-LLM `agent-decide-reimbursement` integration tests to real Groq | User decision — only the new e2e suite uses real Groq; the existing deterministic suite stays as-is.                                                     |
| CI workflow wiring (a scheduled job to run `@pytest.mark.e2e`)          | Not requested; the spec's ACs are about the suite existing and passing locally against a real stack, not about where/when a CI system triggers it.        |
| Compose changes for the third-party services (`postgres`, `kafka`, `langfuse-*`) | None of these run this project's Python code or call `load_dotenv()` — they're not in scope for "the application relies on dotenv"; their `environment:` blocks keep using compose's native `${VAR}` substitution, which is that image's own documented configuration contract. |
| Concurrent multi-item e2e runs                                          | Each e2e test drives one item at a time; concurrent real-LLM calls would add nondeterminism on top of live-model nondeterminism, compounding flakiness.   |
| Re-testing duplicate-item handling or a downed Kafka/Postgres mid-flow at the full e2e level | Already covered at each service's own integration-test level (`publisher`'s duplicate-item test, per-service container-down tests) — redundant to repeat here. |
| Re-testing the LangFuse-unreachable durable-fallback path at the full e2e level | Already unit-tested (`test_langfuse.py`); this spec's traceability AC proves the happy path *with* LangFuse up, not the fallback again.                     |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | --------------- | --------- | ---------- |
| Bundle the dotenv-first config fix and the e2e suite into one spec | One spec, both concerns | User's explicit, twice-reaffirmed choice, overriding the agent's initial recommendation to split. | y |
| `.env` reaches containers via a **runtime, read-only bind mount**, never baked into the image at build time | `./.env:/app/.env:ro` (or equivalent) added to `api`/`publisher`/`reimbursement`/`migrate`'s compose service definitions; `.dockerignore` keeps excluding `.env` from the *build context*, so it never lands in an image layer | User confirmed wanting containers to load `.env` via `python-dotenv` directly, after being shown the tradeoff (a live secrets file becomes reachable inside the running container's filesystem) — a runtime mount (not a build-time `COPY`) is the standard way to get that without baking secrets into a distributable image. | y |
| `KAFKA_BOOTSTRAP_SERVERS`, `DATABASE_URL`, and `LANGFUSE_HOST` stay compose-set (`environment:`), not dotenv-sourced | These 3 keep their current compose values (Docker-network hostnames: `kafka:19092`, `postgres`, `langfuse-web`) | A single `.env` value can't be simultaneously correct for "running inside the Docker network" and "running on the host" (`shared.config`'s own `os.getenv(..., "localhost:9092")` default already assumes the host case) — this is a structural constraint, not a preference, and doesn't contradict "rely on dotenv" for the values that genuinely are environment-independent (secrets, model config, tuning). | y — flagged explicitly, not silently kept |
| `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` handling | `.env.sample` gains a `LANGFUSE_SECRET_KEY=` entry mirroring `LANGFUSE_INIT_PROJECT_SECRET_KEY`'s value (two names, since `LANGFUSE_INIT_PROJECT_SECRET_KEY` is LangFuse's own init-bootstrap var name and can't be renamed); `LANGFUSE_PUBLIC_KEY` (`pk-lf-local-dev`) stays a compose-internal literal, since it is one today (the `x-langfuse-public-key` YAML anchor) and isn't presently a `.env` value at all | Closes the one remaining compose-level name-aliasing case (`LANGFUSE_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}`) so `reimbursement`'s own dotenv load resolves the var it actually reads (`LANGFUSE_SECRET_KEY`) directly from `.env`, with no compose-side renaming in between. | n — reasonable default, flagged here per closure gate |
| "Real docker compose stack" for the e2e suite includes the LangFuse sub-stack | LangFuse (`langfuse-web`, `langfuse-worker`, and its own Postgres/ClickHouse/Redis/MinIO) is brought up as part of the e2e fixture | Part of the same compose file; standing it up is what makes the traceability goal (a queryable real trace) testable at all. | n — reasonable default, flagged here per closure gate |
| No automatic retry/replay around a strict-bucket e2e assertion that fails due to real-model nondeterminism | None — a failure is investigated, not masked | Follows the user's own chosen rationale for "strict bucket assertions." | y |
| ENV-04's parity/coverage guard compares `docker-compose.yml`/`.env.sample` against a maintained list of var names per service, not dynamic source introspection | Hardcoded list, mirroring `test_compose_parity.py`'s existing style | Matches the one precedent already in the codebase for this exact class of test. | n — reasonable default, flagged here per closure gate |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: Every uv-workspace container loads config from a mounted `.env` via `python-dotenv` ⭐ MVP

**User Story**: As a developer, I want `api`, `publisher`, `reimbursement`,
and `migrate` to actually read `.env` at container runtime the same way
they already do on the host, so `.env` is genuinely the single source of
truth everywhere this project runs — not just outside Docker.

**Why P1**: This is the structural fix the rest of the spec depends on;
without it, app config only ever reaches a container through
`docker-compose.yml` duplication, which is exactly the mechanism that has
already drifted (`ENV-03` below).

**Acceptance Criteria**:

1. WHEN `docker-compose.yml`'s `api`/`publisher`/`reimbursement`/`migrate` service definitions are inspected THEN each SHALL include a read-only runtime volume mount making the repo-root `.env` available inside the container at the path `load_dotenv()` resolves from (its working directory or an ancestor of it).
2. WHEN a container built from one of these four services' Dockerfiles is inspected THEN its image SHALL NOT contain a `.env` file — the mount is compose-only and runtime-only; `.dockerignore` continues excluding `.env` from every build context, so a plain `docker build` (no compose) never bakes it in.
3. WHEN `docker compose up -d reimbursement` runs against a `.env` copied verbatim from `.env.sample`, with `GROQ_API_KEY`/`EXTRACT_FIELDS_MODEL_NAME`/`ANALYSIS_MODEL_NAME` present only in that `.env` file and **absent from `docker-compose.yml`'s `environment:` block** THEN the container SHALL reach a running state and stay running for at least 10 seconds (`docker compose ps` reporting `Up`, not `Exited`/restarting) — proving `load_dotenv()` inside the container, not compose passthrough, resolved the required vars.

**Independent Test**: `cp .env.sample .env && docker compose up -d reimbursement && docker compose ps` shows it healthy/running, with `docker-compose.yml`'s `reimbursement` block carrying no `GROQ_API_KEY`/model-name entries at all.

---

### P1: `docker-compose.yml`'s `environment:` blocks are pruned to topology-only values ⭐ MVP

**User Story**: As a developer reading `docker-compose.yml`, I want it to
list only the values that are genuinely different inside the Docker network
versus on the host, so it stops being a second, drift-prone copy of
`.env`'s application config.

**Why P1**: This is the other half of "rely on dotenv, don't propagate over
`docker-compose.yml`" — `ENV-01` makes the mount possible; this story
actually removes the duplication it makes unnecessary.

**Acceptance Criteria**:

1. WHEN `api`'s `environment:` block is inspected THEN it SHALL retain only `KAFKA_BOOTSTRAP_SERVERS` and `DATABASE_URL` (the two Docker-network-topology values) and SHALL NOT list `KAFKA_SECURITY_PROTOCOL`/`KAFKA_SASL_MECHANISM`/`KAFKA_SASL_USERNAME`/`KAFKA_SASL_PASSWORD`/`KAFKA_SSL_CA_LOCATION` — those five now reach the container only via the `.env` mount (`ENV-01`).
2. WHEN `publisher`'s `environment:` block is inspected THEN it SHALL retain only `KAFKA_BOOTSTRAP_SERVERS`/`DATABASE_URL` and SHALL NOT list any of the 5 Kafka-security vars (never had them; still doesn't — the fix is that a value set in `.env` now actually reaches `publisher` via the mount, not by adding compose entries).
3. WHEN `reimbursement`'s `environment:` block is inspected THEN it SHALL retain only `KAFKA_BOOTSTRAP_SERVERS`/`DATABASE_URL`/`LANGFUSE_HOST` and SHALL NOT list `GROQ_API_KEY`, `EXTRACT_FIELDS_MODEL_NAME`, `ANALYSIS_MODEL_NAME`, `AI_TIMEOUT_SECONDS`, `EXTRACT_FIELDS_TEMPERATURE`, `ANALYSIS_TEMPERATURE`, the 5 Kafka-security vars, or `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (the latter two per `ENV-03`, below).

**Independent Test**: Diff `docker-compose.yml` before/after; every removed line's value is still reachable by the container via `ENV-01`'s mount, confirmed by `ENV-01`'s own boot test staying green.

---

### P1: `.env.sample` directly names every var each service's own `load_dotenv()`/`os.getenv()` call reads ⭐ MVP

**User Story**: As a developer, I want `.env.sample` to be a complete,
directly-matching template for what the code actually reads — no
compose-side renaming in between — so copying it and filling in real values
is sufficient on its own, in Docker or out.

**Why P1**: One existing case still breaks "rely on dotenv always":
`reimbursement`'s code reads `LANGFUSE_SECRET_KEY`, but `.env.sample` only
defines `LANGFUSE_INIT_PROJECT_SECRET_KEY` — today's compose block bridges
the two names; once that block is pruned (`ENV-02`), the var needs to exist
under the name the code actually reads.

**Acceptance Criteria**:

1. WHEN `.env.sample` is inspected THEN it SHALL define `LANGFUSE_SECRET_KEY=` alongside the existing `LANGFUSE_INIT_PROJECT_SECRET_KEY=`, both carrying the same placeholder value, so `reimbursement`'s dotenv-loaded config resolves `LANGFUSE_SECRET_KEY` directly without any compose-level rename.
2. WHEN `reimbursement`'s `environment:` block is inspected (per `ENV-02`) THEN it SHALL NOT contain a `LANGFUSE_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}`-style rename.

**Independent Test**: `docker compose up -d reimbursement` still resolves a working LangFuse client (per the existing `test_langfuse.py` unit coverage's own assumptions) with `.env`'s two same-valued vars and no compose-side aliasing.

---

### P1: Automated regression guard for dotenv-first config ⭐ MVP

**User Story**: As a maintainer, I want an automated test that fails loudly
if a future change reintroduces app config into `docker-compose.yml`'s
`environment:` blocks (instead of `.env`), or if `.env.sample` falls out of
sync with what the code actually reads, so this exact class of drift can't
recur unnoticed.

**Why P1**: `ENV-01`–`ENV-03` fix today's instance; without a guard, the
same drift can reappear the moment a service gains a new config var.

**Acceptance Criteria**:

1. WHEN a new automated test (e.g. `test_dotenv_config_parity.py`, alongside the existing `test_compose_parity.py` precedent) runs THEN it SHALL parse `docker-compose.yml` and assert, for each of `api`/`publisher`/`reimbursement`, that its `environment:` block contains **only** that service's maintained topology-only allowlist (`KAFKA_BOOTSTRAP_SERVERS`, `DATABASE_URL`, and — for `reimbursement` — `LANGFUSE_HOST`) — failing with an assertion message naming any unexpected extra var if one is present.
2. WHEN the same test runs THEN it SHALL also assert every name in a maintained required/optional-var list per service (`GROQ_API_KEY`, model names, temperatures, the 5 Kafka-security names, `LANGFUSE_SECRET_KEY`) is present as a key in `.env.sample` — failing with the missing var's name if `.env.sample` and the code's own `os.getenv`/`_require_env` calls drift apart.
3. WHEN this test is run as part of `uv run pytest -m "not integration"` THEN it SHALL be collected and pass, requiring no Docker daemon and no container (pure file parsing, same as `test_compose_parity.py`'s existing pattern).

**Independent Test**: Deliberately re-add `GROQ_API_KEY: ${GROQ_API_KEY}` to `reimbursement`'s compose block locally, confirm the test fails naming it as an unexpected entry, then remove it again.

---

### P1: Analysis guardrail node wires `found_data` and `request_data` into its prompt ⭐ MVP

**User Story**: As a maintainer, I want the `analysis` node (the ambiguous-zone
guardrail, AGD-17..20) to actually populate both `{found_data}` and
`{request_data}` in its prompt template with the fields the prompt itself
documents, so the guardrail can compare the resolved extraction against what
was actually submitted, instead of silently judging on an incomplete or
unsubstituted prompt.

**Why P1**: Discovered mid-implementation of this feature (the prompt in
`prompts/analysis.py` was updated to document a `<found_data>` section with
`currency`/`receipt_date`/`receipt_value` fields and reference
`{found_data}` in its template, but `get_analysis_prompt`/`Analysis.__call__`
never substituted that placeholder and never passed the original payload at
all — only the resolved `extracted` dict, mislabeled as `request_data`).
Without this fix, the guardrail cannot function as the prompt itself
describes, and `E2E-04`/`E2E-05`/`E2E-06`'s real-Groq human-review tests
cannot validate real guardrail behavior — this blocks Phase 3 of the e2e
suite, not just a documentation nit.

**Acceptance Criteria**:

1. WHEN the `analysis` node builds its prompt THEN it SHALL populate the `{found_data}` placeholder with the resolved `extracted` fields rendered under the prompt's own documented names — `currency`, `receipt_date` (from `extracted["receipts_date"]`), `receipt_value` (from `extracted["value"]`) — translated only at this prompt-building boundary; the internal `ExtractedFields` TypedDict and every other consumer of it (`validate.py`, `apply_policies.py`, persistence) keep their existing field names unchanged.
2. WHEN the same prompt is built THEN it SHALL populate the `{request_data}` placeholder with the reimbursement's original submitted payload (`state["reimbursement"].original_payload`), with `submitted_by` (PII) stripped before it reaches the prompt — mirroring `extract_fields`'s existing AGD-26 PII-stripping precedent.
3. WHEN a unit test inspects the rendered prompt content THEN it SHALL assert the found_data's mapped values (`currency`/`receipt_date`/`receipt_value`) and the request_data's payload values (e.g. `raw_ocr_text`, `claimed_amount_brl`) are both present in the rendered prompt, and that `submitted_by`'s value is absent.

**Independent Test**: Unit test (`test_analysis.py`) asserting the rendered `SystemMessage` content contains the found_data's mapped values and the request_data's payload values, and excludes `submitted_by`.

---

### P1: E2E happy path — auto-approve, real Groq, traceable ⭐ MVP

**User Story**: As a developer, I want one real, live-Groq-backed test that a
low-value, fresh reimbursement survives the entire real chain and produces a
traceable auto-approved decision, so the full pipeline's happy path is
provably correct, not just each service's own slice of it.

**Why P1**: This is the core of "e2e test flow for the agent flows" — the
one demonstration that api→publisher→reimbursement actually compose
correctly against a real running stack, and — since that stack now boots
from `.env` alone per the stories above — the first real proof the
dotenv-first config actually works end to end, not just per-container.

**Acceptance Criteria**:

1. WHEN a reimbursement item is POSTed to the real `api` container's `/api/v1/reimbursement` endpoint, with `raw_ocr_text`/`claimed_amount_brl` constructed to extract as a fresh, low (≤200 BRL) value THEN the item SHALL reach `status = "auto-approved"` on the row, observed via `GET /api/v1/reimbursement/:uuid` against the real `api` container, within a bounded polling timeout.
2. WHEN that same decision completes THEN a LangFuse trace SHALL be queryable via LangFuse's own API whose `metadata.langfuse_session_id` equals the reimbursement's `uuid` — the concrete proxy for "this decision is traceable end-to-end," per `CLAUDE.md`'s hard traceability requirement and the existing `agent.py` wiring (`metadata: {"langfuse_session_id": str(reimbursement.uuid)}`).
3. WHEN the same test runs THEN it SHALL be marked `@pytest.mark.e2e` and SHALL fail fast with a clear error (not hang or silently skip) if `GROQ_API_KEY` is unset or the compose stack isn't reachable.

**Independent Test**: Run this one test in isolation (`uv run pytest -m e2e -k auto_approved`) against a running real stack with a real `GROQ_API_KEY` and see it pass end to end.

---

### P1: E2E auto-reject path — real Groq ⭐ MVP

**User Story**: As a developer, I want the same full-chain proof for the
reject branch, so the reject rule (not just the approve rule) is verified
against the real stack.

**Why P1**: Reject and approve are the two deterministic-policy outcomes;
covering only one leaves the other's real-chain wiring unproven.

**Acceptance Criteria**:

1. WHEN a reimbursement item is POSTed with `raw_ocr_text` constructed so its extracted `receipts_date` is more than 90 days before `submitted_at` THEN the item SHALL reach `status = "auto-rejected"` on the row, observed via `GET`, within a bounded polling timeout.
2. WHEN this decision completes THEN `decision_reason` on the row SHALL be non-null, observed via the same `GET` response.

**Independent Test**: `uv run pytest -m e2e -k auto_rejected` passes against the real stack.

---

### P1: E2E human-review path + PUT approve/reject resolution — real Groq ⭐ MVP

**User Story**: As a developer, I want the ambiguous-zone branch and both of
its human-resolution outcomes proven end to end, so the state-transition
from `human-review` to a final approved/rejected status is verified against
the real stack, not only unit-tested in isolation.

**Why P1**: This is the one branch that involves both LLM nodes
(`extract_fields` + `analysis`'s guardrail) and a second, separate API call
(`PUT`) — the highest-surface-area path in the whole system.

**Acceptance Criteria**:

1. WHEN a reimbursement item is POSTed with `raw_ocr_text` constructed to extract into the 200–2000 BRL ambiguous zone with a claimed-amount/OCR inconsistency THEN the item SHALL reach `status = "human-review"` on the row, observed via `GET`, within a bounded polling timeout.
2. WHEN a `PUT /api/v1/reimbursement/:uuid` approval payload is then sent against that same real `api` container THEN the row's `status` SHALL become `"human-approved"`, observed via a subsequent `GET`.
3. WHEN a second, separate item is driven to `human-review` the same way and a `PUT` rejection payload is sent instead THEN that row's `status` SHALL become `"human-rejected"`, observed via `GET`.

**Independent Test**: `uv run pytest -m e2e -k human_review` passes against the real stack, exercising both the approve and reject sub-cases.

---

### P1: E2E retry-ceiling / ghost / stale handling — real chain ⭐ MVP

**User Story**: As a developer, I want the three non-decision branches
(retry ceiling, ghost message, stale message) proven through the real chain
too, so the full "everything" coverage the spec commits to is actually met,
not just the three decision outcomes.

**Why P1**: These three don't reach the LLM nodes at all (the decision graph
short-circuits before them), so they don't carry real-Groq cost/flakiness —
there's no reason to leave them out of the real-chain proof.

**Acceptance Criteria**:

1. WHEN a `Reimbursement` message with `retry = 4` is produced directly to the real Kafka container for a row inserted via the real chain THEN the row SHALL reach `status = "human-review"` with a non-null `decision_reason`, observed via `GET`, without any real Groq call occurring (the retry-ceiling branch short-circuits before the decision graph).
2. WHEN a `Reimbursement` message carrying a `uuid` with no matching row is produced to the real Kafka container THEN the agent SHALL advance its consumer offset past it with no row created and no crash, observed via the container remaining healthy and the next real-chain test in the suite proceeding normally.
3. WHEN a `Reimbursement` message with a `published_at` older than the row's `updated_at` is produced to the real Kafka container THEN the row SHALL remain `status = "pending"` with a null `decision_reason`, observed via `GET`.

**Independent Test**: `uv run pytest -m e2e -k "retry_ceiling or ghost or stale"` passes against the real stack.

---

### P2: E2E suite is opt-in gated and documented

**User Story**: As a contributor running the normal test gate, I want the
real-Groq e2e suite excluded by default, so `uv run pytest` stays free,
fast, and deterministic, and I want the README to say how to run the e2e
suite when I do need it — and to document the dotenv-first config model
this feature establishes.

**Why P2**: Important for the suite to be usable and not accidentally
break the existing gate, but it's process/documentation, not itself part of
proving the pipeline correct.

**Acceptance Criteria**:

1. WHEN `uv run pytest` (the full default gate, per `TESTING.md`) runs THEN it SHALL NOT collect or execute any test marked `@pytest.mark.e2e` — same exclusion mechanism already used for `@pytest.mark.integration` opt-out (`-m "not integration"`), applied as a marker registered in root `pyproject.toml`.
2. WHEN `README.md`/`docs/codebase/TESTING.md`/`docs/codebase/INTEGRATIONS.md` are read THEN they SHALL document: (a) the `e2e` marker, its prerequisites (`docker compose up -d`, a real `GROQ_API_KEY` in `.env`), and the exact command to run it (`uv run pytest -m e2e`); (b) that `.env` is now mounted into and read directly by every uv-workspace container, superseding the old "compose duplicates `.env` values" description.

**Independent Test**: `uv run pytest` (no `-m e2e`) shows the same total collected-test count as before this feature, plus the new dotenv-config-parity test from `ENV-04`; `uv run pytest -m e2e` is a separate, documented invocation.

---

## Edge Cases

- WHEN `GROQ_API_KEY` is unset or invalid at the start of an `e2e`-marked test run THEN the suite SHALL fail fast with a clear, named error — never hang on a live API call or silently skip.
- WHEN the docker-compose stack the e2e fixture depends on isn't already up (or doesn't become healthy within a bounded startup timeout) THEN the fixture SHALL fail with a diagnostic naming which service never became healthy, not hang indefinitely.
- WHEN a strict-bucket e2e assertion fails because a real Groq call landed in an unexpected bucket THEN the test SHALL fail as a normal test failure (the signal is real per the user's own chosen strictness) — no retry-and-suppress logic masks it.
- WHEN `.env` is missing at `docker compose up` time (no `cp .env.sample .env` was ever run) THEN the four mounted-service containers SHALL fail to start with an error traceable to the missing required vars (`_require_env`'s existing `ValueError`), not a confusing unrelated failure — the mount itself doesn't invent a fallback file.

---

## Requirement Traceability

| Requirement ID | Story                                                        | Phase  | Status  |
| --------------- | -------------------------------------------------------------- | ------ | ------- |
| ENV-01          | P1: Containers load config from a mounted `.env` via dotenv    | Design | Pending |
| ENV-02          | P1: `docker-compose.yml` environment blocks pruned to topology-only | Design | Pending |
| ENV-03          | P1: `.env.sample` directly names every var the code reads (LangFuse key) | Design | Pending |
| ENV-04          | P1: Automated regression guard for dotenv-first config         | Design | Pending |
| AGT-01          | P1: Analysis guardrail node wires `found_data`/`request_data` into its prompt | Design | Pending |
| E2E-01          | P1: E2E happy path — auto-approve                               | Design | Pending |
| E2E-02          | P1: E2E happy path — traceability (LangFuse)                    | Design | Pending |
| E2E-03          | P1: E2E auto-reject path                                        | Design | Pending |
| E2E-04          | P1: E2E human-review path (landing)                             | Design | Pending |
| E2E-05          | P1: E2E human-review — PUT approve resolution                   | Design | Pending |
| E2E-06          | P1: E2E human-review — PUT reject resolution                    | Design | Pending |
| E2E-07          | P1: E2E retry-ceiling escalation                                | Design | Pending |
| E2E-08          | P1: E2E ghost-message handling                                  | Design | Pending |
| E2E-09          | P1: E2E stale-message handling                                  | Design | Pending |
| E2E-10          | P2: E2E suite gate exclusion                                    | Design | Pending |
| E2E-11          | P2: E2E suite + dotenv-model documentation                      | Design | Pending |

**ID format:** `ENV-NN` (dotenv-first runtime config), `E2E-NN` (cross-service pipeline flow)

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 15 total, 0 mapped to tasks, 15 unmapped ⚠️ (expected — Tasks phase not yet run)

---

## Success Criteria

- [ ] `cp .env.sample .env && docker compose up -d` brings up `api`, `publisher`, and `reimbursement` all healthy/running, with `docker-compose.yml`'s `environment:` blocks carrying only Docker-network-topology values.
- [ ] Deleting `GROQ_API_KEY`/model-name/Kafka-security vars from `.env` (leaving `docker-compose.yml` untouched) makes the affected container fail to start — proof the vars flow through the `.env` mount, not a compose duplicate.
- [ ] `uv run pytest -m "not integration"` (the quick gate) collects and passes the new dotenv-config-parity test, needing no Docker daemon.
- [ ] `uv run pytest` (the full default gate) has the same pass/fail baseline as before this feature, with zero `@pytest.mark.e2e` tests collected into it.
- [ ] `uv run pytest -m e2e`, run against a live compose stack with a real `GROQ_API_KEY`, passes all of: auto-approve, auto-reject, human-review + both PUT resolutions, retry-ceiling, ghost, and stale.
- [ ] The auto-approve e2e test's LangFuse trace lookup succeeds — proof the traceability requirement holds under a real, full-stack, dotenv-configured run.
