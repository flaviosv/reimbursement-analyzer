# E2E Pipeline Flow & Dotenv-First Runtime Configuration Context

**Gathered:** 2026-08-09
**Spec:** `.specs/features/e2e-pipeline-flow/spec.md`
**Status:** Ready for design

---

## Feature Boundary

Make `.env` the actual runtime source of truth inside containers, not just
on the host: mount it into `api`/`publisher`/`reimbursement`/`migrate` and
let their existing `load_dotenv()` calls resolve config from it directly,
pruning `docker-compose.yml`'s `environment:` blocks down to only the
values that genuinely can't be a shared `.env` value (Docker-network
hostnames). Then add an opt-in, real-stack, real-Groq e2e test suite proving
the full `api → publisher → reimbursement agent → GET/PUT` chain for every
documented outcome branch, plus a real traceability assertion (a queryable
LangFuse trace) — matching `CLAUDE.md`'s hard traceability requirement.

---

## Implementation Decisions

### Spec scope: bundled, not split

The agent recommended splitting this into two specs (a small compose/env
bugfix vs. a large new test-architecture feature), given the size/risk
mismatch. The user explicitly chose to bundle them into one spec instead.
Recorded as an overridden recommendation in `spec.md`'s Assumptions table,
not re-litigated here.

### Config mechanism: dotenv-first, not compose-passthrough (corrected mid-session)

The agent's first draft of this spec fixed only `docker-compose.yml`'s
missing/asymmetric `environment:` entries — i.e. it kept compose's own
`${VAR}` substitution as the delivery mechanism into containers, just
patched the drift. The user explicitly rejected that framing: "I want the
application to rely on dotenv, i don't want propagating over the
docker-compose file... rely on .env always." The corrected design:

- `.env` is mounted read-only into the four uv-workspace containers at
  **runtime** (`docker-compose.yml` volumes), never baked into an image at
  build time — `.dockerignore` still excludes it from every build context.
- Each container's own `load_dotenv()` (already called at every entrypoint)
  becomes the actual resolution mechanism inside Docker too, not just on
  the host.
- `docker-compose.yml`'s `environment:` blocks shrink to **only**
  Docker-network-topology values that cannot be a single shared `.env`
  value regardless of where the process runs: `KAFKA_BOOTSTRAP_SERVERS`,
  `DATABASE_URL`, `LANGFUSE_HOST`.
- The one remaining compose-side name-alias (`LANGFUSE_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}`)
  is closed by adding a second, same-named `.env.sample` entry rather than
  keeping a compose-level rename.

This was surfaced and confirmed via a single follow-up clarifying question
after the user pointed out the first draft under-scoped "the dotenv stuff"
— the agent explicitly named the tradeoff (a live secrets file becomes
reachable inside the running container's filesystem) before the user
confirmed the direction, per this session's own confidence-threshold
practice for non-trivial decisions.

### E2E orchestration: real docker-compose stack

Rejected a lighter testcontainers + in-process-services approach (the
pattern `reimbursement`'s own existing integration suite already uses) in
favor of driving the actual `docker compose up` stack via real HTTP/Kafka
calls from pytest — closer to production topology, exercises the real
Dockerfiles and the compose config this same spec's dotenv-first fix
touches.

### LLM calls: real Groq, only in the new suite

Every e2e test that reaches the two LLM nodes (`extract_fields`, `analysis`)
calls the live Groq API — no fakes in this suite. The existing
`agent-decide-reimbursement` fake-LLM integration tests are explicitly left
untouched (out of scope here) — this is additive coverage, not a
replacement.

### Assertion strictness: strict, not tolerant

Each real-Groq e2e test asserts the exact expected terminal `status`
(`auto-approved`/`auto-rejected`/`human-review`), not a loosened
"a decision was reached" check. A failure from real-model drift or
nondeterminism is treated as a genuine signal to investigate — no automatic
retry-and-suppress around it.

### Gate placement: opt-in marker, excluded from the default gate

`@pytest.mark.e2e`, mirroring the existing `@pytest.mark.integration`
opt-out mechanism. `uv run pytest` (the normal full gate) never collects it
— it needs a live `GROQ_API_KEY`, real API cost, and a running compose
stack, none of which every contributor/PR run should require.

### Flow coverage: all four branch families, no exceptions

Happy path (auto-approve), auto-reject, human-review (both PUT-approve and
PUT-reject resolutions), and the three non-decision branches (retry-ceiling
escalation, ghost message, stale message) are all in scope for this MVP —
the user explicitly rejected a narrower subset.

### Agent's Discretion

- Exact `raw_ocr_text`/payload fixture wording needed to reliably steer the
  live Groq model into each bucket (approve/reject/ambiguous) — left to
  Design/Tasks, since it's an implementation detail of the test fixtures,
  not a product decision.
- Whether the compose/env parity guard test (ENV-04) is a new file
  alongside `test_compose_parity.py` or added to that same file — left to
  Design.
- Bounded-timeout values for e2e polling/health checks — left to Design.

### Declined / Undiscussed Gray Areas → Assumptions

Two points were resolved by the agent's own reasonable default rather than
a further question round (both logged in `spec.md`'s Assumptions table,
flagged as unconfirmed defaults rather than silently assumed):

1. Whether "the real docker-compose stack" includes the LangFuse sub-stack —
   defaulted to yes, since it's part of the same compose file and is what
   makes the traceability goal testable at all.
2. The exact mechanism for ENV-04's parity check (hardcoded required-var
   list vs. dynamic source introspection) — defaulted to the hardcoded-list
   style, matching the one existing precedent (`test_compose_parity.py`).

---

## Specific References

None — no external product/UX reference was invoked; every decision above
traces to either an explicit user answer or existing codebase precedent
(`test_compose_parity.py`, `agent-decide-reimbursement`'s fake-LLM
convention, the `integration` marker's opt-out mechanism).

---

## Deferred Ideas

- A scheduled CI job to run `@pytest.mark.e2e` automatically — came up
  implicitly while discussing gate placement, explicitly deferred (see
  `spec.md`'s Out of Scope table) since it's pipeline/CI infrastructure with
  its own dimension (secrets management for `GROQ_API_KEY` in CI), not
  requested by the user.
- Converting the existing fake-LLM integration tests to real Groq — raised
  and explicitly declined by the user; stays a separate, not-yet-requested
  idea if ever wanted later.
