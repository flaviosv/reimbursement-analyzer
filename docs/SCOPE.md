# Scope

- The service must decide 
	- Which requests can be auto-approved
	- Which must be routed to Human Review
	- Which must be rejected (wth a justification)

# Requirements

## Functional
- **Process reimbursement requests**: Accept reimbursement requests and produce a decision for each
- **Apply business rules**: validate each request against a defined set of business rules
- **Classify decisions**: Each requests must be classified as auto-approved, sent for human review, or rejected
- **Human review**: Cases requiring human judgment must be made available for review, with the reviewer's decision recorded
- **Audit trail**: every decision, automated or human, must be traceable

## Non-functional

- This service is missions-critical.
- It operates in a financial domain, where incorrect decisions may result in financial loss, policy violations, or audit failure
- Given that the service rives monetary decisions based on probabilistic outputs from a LLM, full traceability of all operations is imperative to facilitate auditing and reproducibility

# Approval Policy

- Requests less or equal than 200 may be auto-approved when all validation checks pass
- Requests higher than 2000 must always be routed to human review. This is a hard-requirement
- Receipts dated more than 90 days before the submittions date must be rejected
- State explicitly any assumptions about locale, currency, date format, and how theese thresholds interact with other validation outcomes

# Sample Data Set

- A sample data set is located under docs/original/sample.json

# Technical Scope

![Request lifecycle](assets/request-lifecycle.png)

## Tech Stack

- Python 3.4.7
    - FastAPI 0.141.1
    - LangChain v1.3.0
    - LangGraph v1.2.1
    - Confluent Kafka for Python 2.15.0
    - Pydantic 2.13.4
    - Asyncpg 0.3.10
- PostgreSQL 18
- Kafka 4.3.1 (KRaft, single node — 3.2.1 as originally scoped has no official Docker image; 3.7.0 is the earliest available)
- LangFuse

## Project bootstrap

- Kafka / PostgreSQL must be confirmed working on bootstraping
- Kafka topics must be created
- PostgreSQL DB structure must be created / updated

## Constraints

- (?) Prompts must remove as much as possible PII
- Prompts can't act other than the scope of the task
- Prompts must be created to have 95% of assurance in the answers

## Data Schema

- Use migrations to create the schemas

### Reimbursement

- UUID
- Original payload
- Status
    - Auto-Approved
    - Human-Approved
    - Human Review
    - Auto-Rejected
    - Human-Rejected
- Request ID
- Submited By
- Submited Date
- Reciepets Value
- Reciepets Date
- Currency
- Decision
- Published
- Human Review notes
- Created At
- Updated At

#### Constraints

- Unique constraint against Request ID / submited By together
- Status must be in set of values defined in the schema
- Decision Layer must be in the set of values defiend in the schema
- Submited By must be a valid email
- Required fields
    - UUID
    - Original payload
    - Request ID

### Human Review

- UUID
- Reimbursement UUID©
- Status
    - Approved
    - Rejected
- Review By
- Reason
- Created At

#### Constraints

- Status must be in set of values defined in the schema
- Review By must be valid email

## API


### POST /api/v1/reimbursement
- Publish to the topic `Request` with `retry = 0`
    If fails, reject completely the payload
- Publish to Kafka the entire payload
- **Payload**
    - Accept any payload with max size of 1mb (amended from the original
      25mb — see AD-020 in .specs/STATE.md: sized against
      docs/original/sample.json's ~381 byte average item and the 500-item
      batch cap, leaving ~5.5x headroom per item)
    - Those are the minimum required fields in order to have an acceptable payload
    ```json
    {
        "request_id": "REQ-0001",
        "submitted_by": "ana.silva@company.com",
        "submitted_at": "2026-04-10T09:15:00Z",
        ...
    }
    ```
- **Responses**
    - 201
    - 400
    - 413
    - 500
    - Response body
        ```json
        {
            "msg": ""
        }
        ```

### GET /api/v1/reimbursement
- Pagination (query params)
    - limit 
        - Not required
        - Default = 100
    - offset
        - Not required
        - Default = 0
- Filter (query params)
    - status
        - Values: `auto-approved` | `human-approved` | `human-review` | `auto-rejected` | `human-rejected`
        - Not required
        - Default = empty
        - Accepts more than one value, comma-separated (e.g.
          `status=human-review,auto-rejected`), matching any of the listed
          statuses — see AD-028 in .specs/STATE.md
- Return the last `Human Review` if any
- **Responses**
    - 200 (a filter matching zero rows is still `200` with an empty `data`
      array — the list endpoint never 404s; the `404` originally listed here
      is dropped as a spec error — see AD-027 in .specs/STATE.md)
    - 400
    - 500
        - Response body
        ```json
        {
            "msg": "",
            "data": [
                ...
            ]
        }
        ```

### GET /api/v1/reimbursement/:uuid
- Return a single Reimbursement by the uuid
- **Responses**
    - 200
    - 400
    - 404
    - 500

### PUT /api/v1/reimbursement/:uuid 
- Only recheable for `Reimbursement` in `Human-Rejected` or `Auto-Rejected` or `Human-Review` status
  (confirmed as literally listed — the Decisions section's "not possible to
  Human-Approve if rejected" note below refers to the already-*approved*
  statuses, not to `Human-Rejected` — see AD-027 in .specs/STATE.md)
- Accept approval of rejected `Human Review`
    Do not override the existing record, create a new one
    (means the `human_review` row — already DB-enforced append-only by the
    `human_review_append_only` trigger. `reimbursement.status` is updated in
    place; no second `reimbursement` row is ever created — see AD-027)
- The final status must be propagated to the `Reimbursement Entity`
- For an approval, all the fields from the payload are required
  (this is also the completeness gate for `receipts_value`/`receipts_date`/
  `receipts_currency` — those columns being NULL on a `Human Review` row is
  expected, and this payload rule is what backfills them; no additional
  application-level gate exists beyond it — see AD-027)
- For rejecting, just the `reason` and `approved_by` are required
  (the reject payload has no field for `receipts_value`/`receipts_date`/
  `receipts_currency` — so, unlike approval, rejecting does not backfill
  them. A reject is blocked with `400` unless all three are already
  non-null on the `Reimbursement` entity, so a finalized record is never
  left incomplete — see AD-027 addendum in .specs/STATE.md)
- **Payload**
    ```json
    {
        "uuid": "",
        "status": "approved | rejected",
        "reason": "",
        "receipts_date": "yyyy-mm-dd",
        "receipts_value": 0.0,
        "receipts_currency": "",
        "approved_by": ""
    }
    ```
- **Responses**
    - 200
    - 404 (amended — added: unknown `uuid`. Not in the original list despite
      PUT operating on a path parameter — see AD-027 in .specs/STATE.md)
    - 422
    - 400
    - 500
    - Error body
        ```json
        {
            "msg": ""   
        }
                ```

## Reimbursement Publisher

- It consumes the topic `Request`
- Iterate over each item from the message and, in a single transaction. 
    - Create to the DB the Reimbursement with `UUID` and `Original Payload` 
    - Publish to the topic `Reimbursement`

### Error handling

- If the `retry > 3`, it means it's failing repeatedly
    - Try to create the `Reimbursement` with Human Review status
    - Fallback to log the error in a file if fails
    - No other action below must be done
- If the DB insert fails, no publish to the topic must happen
- If any the publish to topic fails, it must rollback the DB transaction
- If any error happens, it must republish incrementing the retry

## Reimbursement Agent

![Reimbursement processing](assets/reimbursement-processing.png)

### Constraints

- If the message's `Published Data` is lower than the the `Reimbursement Updated At`, ignore the message

### Reject

- If `Receipt Date`is older than 90 days, instantly reject
  (this rule always wins — it is evaluated first, ahead of the `>2000`
  mandatory human-review rule below, so an old *and* large receipt is
  rejected, not routed to human review — see AD-030 in .specs/STATE.md)

### Auto-Approved

#### Deterministic layer
- If the payload matches the sample and the minimum required fields are there, move to the deterministic layer, if the required fields to create a minimum Reimbursement entity are not detectable, move to the Probabilistic Layer
  (amended — this conditional branching is superseded: the Probabilistic
  layer's extraction step below now runs unconditionally, on every
  Reimbursement, before this deterministic check — not gated behind field
  absence. The deterministic layer instead validates completeness and
  applies the policy thresholds against the extraction step's output — see
  AD-030 in .specs/STATE.md)
- Minimum required fields (for correct traceability and reimbursement processing)
    - Request ID
    - Submited By
    - Requested Value
    - Submited Date
    - Currency
        - Hardcoded as BRL as the `claimed_amount_brl` would be present
          (amended — `claimed_amount_brl` being present no longer skips
          the extraction step below; currency is resolved through that
          same unconditional step, still fixed to BRL — see AD-030)

#### Probabilistic layer
- an LLM / SLM must evaluate, by the sent payload, if there are enough data to create a `Reimbursement` entity, returning as json via structured output. 
  (amended — this extraction step runs first and unconditionally on every
  Reimbursement, not only when the deterministic layer can't detect
  required fields. Its internal composition — whether it pre-fills a
  minimum object from directly-readable fields like `claimed_amount_brl`
  before calling the LLM, or resolves everything through the LLM in one
  pass — is intentionally left open, a Design-phase decision — see AD-030)
- To avoid mistakes by the LLM, do not rely on it to determine if it's auto-approved or rejected. If it doesn't return the enough data to create the `Reimbursement`, move to `Human Review`
- Find out a reasonable LLM size for it, a big model might not be necessary in here, consider SLM to reduce the cost
- One prompt per `Reimbursement`, to prevent LLM from hallucinating
  (clarified — one prompt per extraction call: the extraction step resolves
  value, currency, and receipt date together in a single call, not batched
  across Reimbursements. A second, separate call — the guardrail/judge
  below — is its own single prompt, invoked only for the ambiguous
  `200 < value ≤ 2000` zone — see AD-030)
- (?) Add guardrails in order to validate if the data present in the payload is not contraditory internally, i.e
    - if the `claimed_category` doesn't match the Type of the business by name in `raw_ocr_text`
    - If there are more than one reference to the fields to be found detailed below
  (the specific guardrail checks are intentionally left undetermined — this
  scope only requires that a consistency-check step exists and gates
  ambiguous-zone auto-approval; see `.specs/features/agent-decide-reimbursement/spec.md`)
- Fields to be found 
    - Receipts Date
    - Receipts Value
    - Currency
- If any the values are found, rely on the deterministic layer to auto approve or reject
    - Use LLM as Judge to verify if the found data matches what is expected to create the Reimbursement
- If none of the values are found, send instantly to `Human Review`
  (clarified — this applies per-field: an unresolved requested value or an
  unresolved receipt date each independently route to `Human Review` rather
  than being auto-approved or auto-rejected on missing data — see
  `.specs/features/agent-decide-reimbursement/spec.md`)

### Human review
- `Requested Value > 2000` must go immediately to `Human Review`
    - `Receipt Date` must be newer than 90 days
- Run a LLM check using a cheap and fast model help out the human review, adding a side note with the LLM output
  (whether every `Human Review` outcome gets an LLM-generated note, or only
  this `>2000` case, is deferred alongside the error-handling mechanism
  below — cost-sensitive, evaluated in a later session — see AD-030 and
  R-011 in .specs/STATE.md / .specs/RISKS.md)

### Error Handling

- If the `retry > 3`, it means it's failing repeatedly
    - Try to create the `Reimbursement` with `Human Review` status
    - Fallback to log the error in a file if fails
- If any error happens, rollback any DB transactions and republish incrementing the `retry`
  (amended — for the decision stage specifically, this retry-then-escalate
  mechanism is not yet adopted as-is: retrying a billed LLM call is not
  free like retrying a DB query, so the exact mechanism is deferred to a
  later, cost-aware evaluation — tracked as R-011 in .specs/RISKS.md. This
  section's mechanism still governs the resolve stage
  (`agent-consume-reimbursement`, already shipped) unchanged)

## Audit

### Traceability

- Add LangFuse support to make with fallback to file logging with the steps being taken

# Documentation

- Add support to Swagger

# Decisions

### Authentication
- Didn't add authentication to allow focus on the business rule
- The Human Review data schema contains an Reviewed By, what keeps a plain text email, even though the endpoint is receiving an email, the correct approach would be extract from the JWT, as example, or other method relying on the authentication step

## API Layer

### Request
- The project assume that the payload can vary, just a set of fields has been set as required, for the sake of requester's identity
    - `request_id`
    - `submitted_by`
    - `submitted_at`
- Considering the project is missions critical, no requests must be lost, so the `POST /api/v1/reimbursement` will publish to a Kafka topic to minimize the loses. The internal processing will make sure the requests will be fully processed or monitored in case of errors
- Even though the attachmets could be used to validate if the `raw_ocr_text` matches the data in the attachment, I deliberatedly decided to leave this feature out of the scope to reduce the complexity

### Cache
- No cache has been applied, no reason for adding it

## Agent Layer

### Deterministic
- It's gonna exist to either support the Agent as a tool or to simply move to the correct status if no LLM are needed at all

### Probabilistic Layer
- The Agent starts with the task of finding the `Receipt Date`, this project prioritize the `Reject First`.
    - The sample payload doesn't contain a `Receipt Date`, and the document is explicity that the payload can vary
- The LLM is getting two responsabilities
    - Locate all the required information to deterministically auto-approve or reject (Currency, `Receipt Date` and `Receipt Value`)
    - Run analysis in order to fill the gap, where if the `Reimbursement`is not rejected, and `20 > Receipt Value < 2000`. 
        - The agent is gonna if the information is solid and not contradictory, with the intetion of covering the gap from the document where. 
        - i.e the category is hotel but the service name is "Mexican Restaurant", if this happens, then it's gonna send to `Human Review`
- Not reject / discard requests if errors
    - If repeat errors / edge scenarios happens, immediately is gonna be sent to Human Review, to avoid losing request
    - Fallback to logging in the output if any services are unavailable (DB / Kafka)
    - This decision can polute the `Human Review` flow, however monitoring tools can be added in order to evaluate the impact in this flow and apply other approaches after real data comes in

## Human Review
- If the a `Human-Approve` operation gappens without the fields below, it's gonna be blocked
    - Currency
    - Receipt Date
    - Receipt Value
    - Reason
-  It's possible to `Human-Approve`if the `Reimbursement`is in `Human-Rejected` or `Auto-Rejected` or `Human-Review`
    - It has been decided to let the `Auto-Rejected` to ba `Human-Approved` to allow the system recover from the possible agent / human errors
- It's not possible to `Human-Approve` if the `Reimbursement` is rejected, as it's not possible assure the system can get back the money 
    - This is an edge caase not covered by this project

## General Decisions
- This project took over 30h, definitely higher than the expectation from the document
    - I assumed this project as a professional one as requested by the documentation, taking care of as many details as i could in the deadline i had
- AI Usage
    - This document, and the overral architecture was defined by me
    - The implementation was done wit the following flow
        - Grilling Sessions (if needed)
        - SDD 
        - AI Code review
        - Manual review limited due to the deadline
    - I decided to rely heavily on agentic engineering due to world's reality
- Manual intervention
    - All the planning phase has been done manually, with grilling sessions at the end to refinement
    - The code has been validated manually as well, however not in a deep level, otherwise there wouldn't have enough time
    - The prompts has been done manually with AI validation
    - The architecture has strong personal decision with AI suggestions
- Tests
    - All the unit / integration tests will be generated by AI, there wouldn't have enough time to write them down carefuly
- The document [Risks.md](RISKS.md) contains some aspects that i've identified during this test development, however I didn't have enough time to take care of them all

## Architecture decisions
- **All or nothing**: The API layer receives the request and publish to Kafka, the first Consumer create the records in DB then, a second consumer send over to the agent
     - If any of the reimbursements in the request has a bad payload, all the request is rejected
     - Duplicated requests are rejected and logged
- Reject first deterministically, Human Review if any doubts
    - If the `Receipt Date` older than 90 days, reject immediately
    - Any other cases that can't approve, it's gonna be sent to `Human Review`
        The decision on this is to initially monitor the agent and see what the `Human Review` is receiving. A Phase 2 can collect data and add new approaches in order to auto reject more accurately
- Async processing
    - It has been chosen event-driven architecture to make the system scalable, each consumer can scale as the requests are indepotent
- Observability
    - LangFuse support added for tracing the agent
    - Regular output so other tools can read it and monitor
    - Assuming monitoring tools watching the output to gather metrics

## What i would have done with extra time
- Run a detailed code review, making sure the architecture / design has better practices
    - I fully relied on agentic engineering, running code reviews, but the codebase is not small
- I would have review the [Risks.md](RISKS.md), making sure would be ready for a launch
- The prompts can receive better instructions and testing
- I would have added authentication
- Tested small / bigger models, in order to evaluate performance x cost x accuracy
- The prompts are receiving the full payload, if by any chance it receives just one payload with 1MB, the cost of tokens will not worth probably

## What i would have done better
- Even though the [Scope.md](SCOPE.md) was made completely by me, I decided to start with no architecture pre-defined, due to that, need to run rounds of refactoring and i wasted at least 4h-6h on it
- I would have prepared better my harness for this project, i made several adjusments to improve the performance and the cost savings in order to have better and performatic results
- Improved the Human Review experience

# Next Phases

## Phase 2
Those are possible Phase 2 tasks 
- Authentication
- Human Review evaluator
    - Identify what are the most gaps going to Human Review, in order to improve the agent capabilities
- Add an event structure to trigger to other topic when an event happen
- Improve the deterministic layer, reducing the chance of going to the probabilistic layer, saving tokens and resources