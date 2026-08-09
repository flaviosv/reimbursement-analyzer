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

### Auto-Approved

#### Deterministic layer
- If the payload matches the sample and the minimum required fields are there, move to the deterministic layer, if the required fields to create a minimum Reimbursement entity are not detectable, move to the Probabilistic Layer
- Minimum required fields (for correct traceability and reimbursement processing)
    - Request ID
    - Submited By
    - Requested Value
    - Submited Date
    - Currency
        - Hardcoded as BRL as the `claimed_amount_brl` would be present

#### Probabilistic layer
- an LLM / SLM must evaluate, by the sent payload, if there are enough data to create a `Reimbursement` entity, returning as json via structured output. 
- To avoid mistakes by the LLM, do not rely on it to determine if it's auto-approved or rejected. If it doesn't return the enough data to create the `Reimbursement`, move to `Human Review`
- Find out a reasonable LLM size for it, a big model might not be necessary in here, consider SLM to reduce the cost
- One prompt per `Reimbursement`, to prevent LLM from hallucinating
- (?) Add guardrails in order to validate if the data present in the payload is not contraditory internally, i.e
    - if the `claimed_category` doesn't match the Type of the business by name in `raw_ocr_text`
    - If there are more than one reference to the fields to be found detailed below
- Fields to be found 
    - Receipts Date
    - Receipts Value
    - Currency
- If any the values are found, rely on the deterministic layer to auto approve or reject
    - Use LLM as Judge to verify if the found data matches what is expected to create the Reimbursement
- If none of the values are found, send instantly to `Human Review`
- 

### Human review
- `Requested Value > 2000` must go immediately to `Human Review`
    - `Receipt Date` must be newer than 90 days
- Run a LLM check using a cheap and fast model help out the human review, adding a side note with the LLM output

### Error Handling

- If the `retry > 3`, it means it's failing repeatedly
    - Try to create the `Reimbursement` with `Human Review` status
    - Fallback to log the error in a file if fails
- If any error happens, rollback any DB transactions and republish incrementing the `retry`

## Audit

### Traceability

- Add LangFuse support to make with fallback to file logging with the steps being taken

# Documentation

- Add support to Swagger

# Decisions

- Request
    - The project assume that the payload can vary, just a set of fields has been set as required, for the sake of requester's identity
        - `request_id`
        - `submitted_by`
        - `submitted_at`
    - Considering the project is missions critical, no requests must be lost, so the `POST /api/v1/reimbursement` will publish to a Kafka topic to minimize the loses. The internal processing will make sure the requests will be fully processed or monitored in case of errors
    - Even though the attachmets could be used to validate if the `raw_ocr_text` matches the data in the attachment, I deliberatedly decided to leave this feature out of the scope to reduce the complexity
- Authentication
    - Didn't add authentication to allow focus on the business rule
    - The Human Review data schema contains an Reviewed By, what keeps a plain text email, even though the endpoint is receiving an email, the correct approach would be extract from the JWT, as example, or other method relying on the authentication step
- Tests
    - All the unit / integration tests will be generated by AI and validated manually due to the time available to complete the test
- Observability
    - LangFuse support added for Agent's tracing
    - Regular logging output so other tools can read it
    - Assuming monitoring tools are in place to detect errors in any layers and monitoring the number of human reviewes being placed
- Async processing
    - It has been chosen event-driven architecture to make the system scalable, each consumer can scale as the requests are indepotent
- Reimbursement Agent
    - Deterministic Layer
        - It's gonna exist to either support the Agent as a tool or to simply move to the correct status if no LLM are needed at all
    - Probabilistic Layer
        - The LLM is getting two responsabilities
            - Find the information in a payload different of the expected format
            - If there are enough information found in the payload, use the LLM to evaluate if the information is solid and not contradictory, with the intetion of covering the gap from the document where
                - `Value < 200`: Automatically approves
                - `Value < 200`: Send for Human Review
        - Reject only `Receipt Date` older than 90 days
            - If the probabilistic layer find all the required information to evaluate the status and the default policies can't be automatically applied, send to Human Review
            -
        - Not reject / discard requests if errors
            - If repeat errors / edge scenarios happens, immediately is gonna be sent to Human Review, to avoid losing request
            - Fallback to logging in the output if any services are unavailable (DB / Kafka)
            - This decision can polute the `Human Review` flow, however monitoring tools can be added in order to evaluate the impact in this flow and apply other approaches after real data comes in
- Data Extraction
    - It wasn't considered in this project the possible extraction of data from `raw_ocr_text` or other possible fields via probabilistic layer / ETL, even though would be a good practice for Data Analyzes
- Human Review
    - If the system tries to `Human-Approve` without the fields below present in the `Reimbursement` entity, it's gonna be blocked, all those fields are mandatory to have the data consistent
      (resolved: no fields beyond what's already required — the PUT payload's
      own required-for-approval fields backfill `receipts_value`/
      `receipts_date`/`receipts_currency`, and `submitted_by`/`submitted_at`
      are already guaranteed non-null by `POST /api/v1/reimbursement`'s own
      validation. No separate application-level completeness gate exists —
      see AD-027 in .specs/STATE.md)
    -  It's possible to `Human-Approve`if the `Reimbursement`is in `Human-Rejected` or `Auto-Rejected` or `Human-Review`
        - It has been decided to let the `Auto-Rejected` to ba `Human-Approved` to allow the system recover from the possible agent errors
    - It's not possible to `Human-Approve` if the `Reimbursement` is rejected, as it's not possible assure the system can get back the money 
        - This is an edge caase not covered by this project
        (clarified: "rejected" here means the already-*approved* statuses —
        `Auto-Approved`/`Human-Approved` — since only disbursed money can't be
        clawed back. `Human-Rejected` remains PUT-eligible per the bullet
        above — see AD-027 in .specs/STATE.md)


- Adding failures to the log is an ultimate resource and must be monitores by tools such as fluentd
- The solutions started to become more complex than i could follow up, so i decided to run a round simplyfing the implementatin if applicable

# Phase 2
- Human Review Evaluator
    - Identify what are the most gaps going to Human Review, in order to improve the agent
- Add an event structure to trigger to other topic when an event happen
- Improve the deterministic layer, reducing the chance of going to the probabilistic layer, saving tokens and resources
- Add loading test
- Add E2E testing
- Add Authentication

# Let the LLM decide a few things
- It's possible starting with a small model, reducing cost, as performance is not a hard requirement at the moment
- If the claimed_category matches the raw_ocr_text data
- If the claimed_amount_brl matches what is described in the raw_ocr_text

# Technical Decisions
- Use a Python library that retries automatically if fails
- Make sure kafka can handle a 1mb message, to respect the validation
  (amended from 25mb — see AD-020 in .specs/STATE.md)