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

## Tech Stack

- Python 3.4.17
- PostgreSQL 18
- Kafka 3.2.1
- FastAPI 0.141.1

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
    - Human Review
    - Rejected
- Request ID
- Submited By
- Submited Date
- Reciepets Value
- Reciepets Value
- Currency
- Decision Layer
    - Deterministic
    - Probablistic
- Final Decision
- Human Review notes
    - Choose a better name
- Published
- Created At
- Updated At

#### Constraints

- Unique constraint agains Request ID / submited By
- Status must be in set of values defined in the schema
- Decision Layer must be in the set of values defiend in the schema
- Submited By must be a valid email
- Required fields
    - UUID
    - Original payload
    - Request ID

### Human Review

- UUID
- Reimbursement UUID
- Status
    - Approved
    - Rejected
- Approved By
- Reason
- Created At

#### Constraints

- Status must be in set of values defined in the schema
- Approved by must be valid email

## API


### POST /api/v1/reimbursement
- Publish to the topic `Request` with `retry = 0`
    If fails, reject completely the payload
- Publish to Kafka the entire 
- **Payload**
    - Accept any payload with max size of 25mb
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
        - Values: `auto-approved` | `human-review` | `rejected`
        - Not required
        - Default = empty
- Return the last `Human Review` if any

### PUT /api/v1/reimbursement/:uuid 
- Only recheable for `Reimbursement` in `Human Review` status
- Accept approval of rejected `Human Review`
    Do not override the existing record, create a new one
- **Payload**
    ```json
    {
        "uuid": "",
        "status": "approved | rejected",
        "reason": "",
        "receipts_date": 0.0,
        "receipts_value": 0.0,
    }
    ```
- **Responses**
    - 200 - Processing error body
    - 422 - Error body
    - 400 - Error body
    - 500 - Error body
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

## Reimbursement Processor

### Constraints

- If the message's `Published Data` is lower than the the `Reimbursement Updated At`, ignore the message

### Auto-Approved / Rejected 

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

### Human review
- `Requested Value > 2000` must go immediately to `Human Review`
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

# Items to add to the definitions

- This project has the assumption that the payload provided is a sample only and can vary, otherwise no probabilistic (LLM / SLM) layer would be required
- Didn't add authentication to allow focus on the business rule
    - The Human Review data schema contains an Approved By, what keeps a plain text email, even though the endpoint is receiving an email, the correct approach would be extract from the JWT as example or other method relying on the authentication
- I could use a event-driven architecture, however there aren't any requirement justifying it. 
    - Taking a simpler approach that can be escalated horizontally
- All the unit / integration tests has been done using AI with manual verification
- LangFuse support added for traceability
- Choose the path of not relying on the LLM to determine if it should be approved or not
    - Started with eh assumption that payloads with different format can arrive to the endpoint
    - It's possible starting with a small model, reducing cost, as performance is not a hard requirement at the moment
    - The fact that is a mission critical service we can metrify / monitor the number of requests sent to human review to detect if it's requiring too much human intervention, if it's the case, a new approach relying on the LLM to approve could be placed
- It wasn't considered in this project the possible extraction of data from `raw_ocr_text` or other possible fields via probabilistic layer / ETL, even though would be a good practice for Data Analyzes
- Missing Request ID, Submited By, Submited Name and Requested Value moves to Human Review, if in the Human Review it's not possible to determine those values, the Human won't be able to approve, as the traceability hard requirement wouldn't be fullfilled
- It's possib
- The Layer field in the Reimbursement entity represents if the status was defined by the Deterministic or Probabilistic layer
- It's not possible decline an Human Review if the Reimbursement is in Auto Approved status, or the Human Review in Approved status, as it's not possible get the value from the requester account
    - This is an edge caase not covered by this project
- None of the samples contains data as the Bank Account information, so this project got the assumption other tool could consume the information from this service and perform the reimbursement by the Submited By
- Considering the project is missions critical, no requests must be lost, so the `POST /api/v1/reimbursement` will add to a Kafka topic to minimize the loses, so the `Reimbursement` can be processed async
- The decision to reject completely the payload if publishing to the topic fails when sending the `Reimbursements` is to reduce the complexity of the internal structure, such as the need of adding retries strategies and some validations across the process. Monitoring tools must be in place to detect critical problems in either the endpoint app or Kafka
- The justification to accep a minimum payload is to make sure the request is identificable, the other information we can use the deterministic or probabilist layer to figure out the values
    - Consider accepting a pattern for the other fields
- Assuming monitoring tools are in place to detect errors in any layers and monitoring the number of human reviewes being placed
- Even though the attachmets could be used to validate if the `raw_ocr_text` matches the data in the attachment, I deliberatedly decided to leave this feature out of the scope to reduce the complexity
- Assumed the claim_amount_brl it's the total of the `raw_ocr_text`
- Any payload acceptable with the minimum requirements from the endpoint, to match the documentation where said that the sample payload is just a sample and doesn't reflect all the possibilities
     - The minimum payload defined in order to make sure it's possible identify who is sending the request and have idepotent requests

# Decisions to take
- Missing the guidelines of the gap between 200 and 2000
- Add an evaluator of issues happening for the Human Review?
- The human review status must reflect into the Reinbursement?
- Lack of Receipts date must send to Human Review

# Let the LLM decide a few things
- If the claimed_category matches the raw_ocr_text data
- If the claimed_amount_brl matches what is described in the raw_ocr_text

Technical Decisions
- Use a Python library that retries automatically if fails
- Make sure kafka can handle a 25mb message, to respect the validation