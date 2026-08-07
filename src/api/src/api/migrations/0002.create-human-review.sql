-- depends: 0001.create-reimbursement

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

-- An audit trail the application can quietly rewrite is not an audit trail.
CREATE FUNCTION reject_human_review_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'human_review is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER human_review_append_only
    BEFORE UPDATE OR DELETE ON human_review
    FOR EACH ROW EXECUTE FUNCTION reject_human_review_mutation();
