import psycopg


class DescribeTheWorkspacePostgresFixture:
    def it_is_reachable_from_the_shared_test_tree(self, migrated_db: str) -> None:
        # Proves the fixture resolves from outside src/api/tests/, which is
        # what keeps shared's Postgres-backed tests from starting a second
        # container.
        with psycopg.connect(migrated_db) as connection:
            assert connection.execute("SELECT 1").fetchone() == (1,)

    def it_hands_out_a_migrated_schema(self, migrated_db: str) -> None:
        with psycopg.connect(migrated_db) as connection:
            row = connection.execute("SELECT to_regclass('public.reimbursement')").fetchone()

        assert row is not None and row[0] == "reimbursement"
