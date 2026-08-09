-- depends: 0002.create-human-review

-- The no-filter GET path (`$1::text[] IS NULL`) has no status predicate, so
-- the (status, created_at DESC) index from 0001 cannot serve a globally
-- created_at-ordered scan — Postgres groups by status first. This index
-- lets that path use a backward index scan instead of a sequential scan
-- plus explicit sort.
CREATE INDEX reimbursement_created_idx
    ON reimbursement (created_at DESC);
