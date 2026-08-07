--
-- file: migrations/0001.create-reimbursement.sql
--
-- The publisher inserts a row carrying only uuid + original_payload +
-- request_id; every other column is filled in later by the agent. That is why
-- almost everything here is nullable.
--

CREATE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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

    -- NULLS NOT DISTINCT is required, not stylistic: submitted_by is optional,
    -- and under standard NULL semantics unlimited rows could share a
    -- request_id whenever the submitter is absent.
    CONSTRAINT reimbursement_request_submitter_key
        UNIQUE NULLS NOT DISTINCT (request_id, submitted_by)
);

CREATE INDEX reimbursement_status_idx ON reimbursement (status);

-- The agent skips a message whose published date predates updated_at, so an
-- application that forgets to bump updated_at would cause silent reprocessing.
CREATE TRIGGER reimbursement_set_updated_at
    BEFORE UPDATE ON reimbursement
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
