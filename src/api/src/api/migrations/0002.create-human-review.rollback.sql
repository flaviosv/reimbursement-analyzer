--
-- file: migrations/0002.create-human-review.rollback.sql
--

DROP TABLE human_review;

DROP FUNCTION reject_human_review_mutation();
