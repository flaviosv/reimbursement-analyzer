--
-- file: migrations/0001.create-reimbursement.rollback.sql
--

DROP TRIGGER reimbursement_set_updated_at ON reimbursement;

DROP TABLE reimbursement;

DROP FUNCTION set_updated_at();
