from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.validation import QueryValidationError, UsageError, get_query_text, validate_query


class ValidationTests(unittest.TestCase):
    def test_select_with_trailing_semicolon_is_allowed(self) -> None:
        validated = validate_query("SELECT 1;", "sqlite")
        self.assertEqual(validated.normalized_text, "SELECT 1")
        self.assertEqual(validated.statement_type, "select")

    def test_cte_select_is_allowed(self) -> None:
        validated = validate_query(
            "WITH recent AS (SELECT 1 AS id) SELECT id FROM recent",
            "postgres",
        )
        self.assertEqual(validated.statement_type, "select")

    def test_insert_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("INSERT INTO users VALUES (1)", "mysql")

    def test_select_into_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("SELECT * INTO OUTFILE '/tmp/demo' FROM users", "mysql")

    def test_multiple_query_sources_raise_usage_error(self) -> None:
        class Args:
            query_text = "SELECT 1"
            query = "SELECT 2"
            sql_file = None

        with self.assertRaises(UsageError):
            get_query_text(Args(), None)
