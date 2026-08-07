--
-- file: migrations/0002.create-human-review.sql
--
-- depends: 0001.create-reimbursement
--
-- Append-only: a new review decision creates a new row rather than
-- overwriting the previous one, so there is no updated_at column.
--

CREATE TABLE human_review (
    uuid                UUID PRIMARY KEY DEFAULT uuidv7(),
    reimbursement_uuid  UUID NOT NULL
        REFERENCES reimbursement (uuid) ON DELETE RESTRICT,
    status              TEXT NOT NULL,
    reviewed_by         TEXT NOT NULL,
    reason              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT human_review_status_check CHECK (
        status IN ('approved', 'rejected')),

    CONSTRAINT human_review_reviewed_by_check CHECK (
        reviewed_by ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
        AND length(reviewed_by) <= 254)
);

-- Serves "return the last Human Review if any" on the reimbursement listing.
CREATE INDEX human_review_reimbursement_created_idx
    ON human_review (reimbursement_uuid, created_at DESC);
