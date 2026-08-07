CREATE TABLE reimbursement (
    uuid                UUID PRIMARY KEY DEFAULT uuidv7(),
    request_id          TEXT NOT NULL,
    submitted_by        TEXT,
    submitted_at        TIMESTAMPTZ,
    original_payload    JSONB NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    receipts_value      NUMERIC(14,2),
    receipts_date       DATE,
    currency            TEXT,
    decision_reason     TEXT,
    human_review_notes  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT reimbursement_status_check CHECK (status IN (
        'pending',
        'auto-approved', 'auto-rejected',
        'human-review',
        'human-approved', 'human-rejected')),

    CONSTRAINT reimbursement_submitted_by_check CHECK (
        submitted_by IS NULL
        OR (submitted_by ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
            AND length(submitted_by) <= 254)),

    CONSTRAINT reimbursement_currency_check CHECK (
        currency IS NULL OR currency ~ '^[A-Z]{3}$'),

    CONSTRAINT reimbursement_request_id_length_check CHECK (
        length(request_id) <= 64),

    -- No upper bound on purpose: an implausibly large claim must still be
    -- stored so the decision layer can reject it with an auditable reason.
    CONSTRAINT reimbursement_receipts_value_check CHECK (
        receipts_value IS NULL OR receipts_value >= 0),

    CONSTRAINT reimbursement_receipts_date_check CHECK (
        receipts_date IS NULL OR receipts_date <= CURRENT_DATE),

    -- The hour of slack absorbs clock skew between the submitting service and
    -- the database.
    CONSTRAINT reimbursement_submitted_at_check CHECK (
        submitted_at IS NULL OR submitted_at <= now() + interval '1 hour')
);

-- lower(): email local-parts are case-insensitive at every real provider, so a
-- byte-exact key lets one person file the same request_id under ana@, Ana@ and
-- ANA@ and be paid three times. NULLS NOT DISTINCT covers the same leak when
-- the submitter is absent.
CREATE UNIQUE INDEX reimbursement_request_submitter_key
    ON reimbursement (request_id, lower(submitted_by)) NULLS NOT DISTINCT;

-- Status alone is far too unselective to beat a sequential scan; the listing
-- endpoint needs the created_at ordering it pages on.
CREATE INDEX reimbursement_status_created_idx
    ON reimbursement (status, created_at DESC);
