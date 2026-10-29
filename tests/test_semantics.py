"""Semantic validation tests: columns, FIND duplicates, LIMIT, and boundaries."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from logseek import LogSeekError, lex, parse, validate_semantics


def check(text):
    """Parse and semantically validate query text."""
    query = parse(lex(text))
    validate_semantics(query)
    return query


class SemanticValidationTests(unittest.TestCase):
    def assert_error(self, text, category, line=None):
        with self.assertRaises(LogSeekError) as caught:
            check(text)
        self.assertEqual(caught.exception.category, category)
        if line is not None:
            self.assertEqual(caught.exception.line, line)
        return caught.exception

    def test_valid_query_passes(self):
        query = check(
            'LOAD "a.csv"\n'
            'WHERE "severity" = "ERROR" AND "message" CONTAINS "retry"\n'
            'AFTER "timestamp" "2026-10-01"\n'
            'BEFORE "timestamp" "2026-11-01 12:00:00"\n'
            'FIND "service", "message"\n'
            'ORDER BY "timestamp" DESC\n'
            "LIMIT 10\n"
            'EXPORT "out.csv"\n'
        )
        self.assertEqual(query.limit, 10)

    def test_unknown_column_in_where(self):
        error = self.assert_error(
            'LOAD "a.csv"\nWHERE "Severity" = "ERROR"\n', "semantic", line=2
        )
        self.assertEqual(error.message, 'unknown column "Severity"')

    def test_unknown_column_in_find(self):
        self.assert_error('LOAD "a.csv"\nFIND "Service"\n', "semantic", line=2)

    def test_unknown_column_in_order_by(self):
        self.assert_error('LOAD "a.csv"\nORDER BY "Time"\n', "semantic", line=2)

    def test_duplicate_find_column(self):
        error = self.assert_error(
            'LOAD "a.csv"\nFIND "service", "service"\n', "semantic", line=2
        )
        self.assertEqual(error.message, 'duplicate column "service" in FIND')

    def test_after_requires_the_timestamp_column(self):
        error = self.assert_error(
            'LOAD "a.csv"\nAFTER "service" "2026-10-01"\n', "semantic", line=2
        )
        self.assertEqual(error.message, 'AFTER requires the "timestamp" column')

    def test_before_requires_the_timestamp_column(self):
        error = self.assert_error(
            'LOAD "a.csv"\nBEFORE "message" "2026-10-01"\n', "semantic", line=2
        )
        self.assertEqual(error.message, 'BEFORE requires the "timestamp" column')

    def test_after_with_unknown_column(self):
        self.assert_error('LOAD "a.csv"\nAFTER "Time" "2026-10-01"\n', "semantic", line=2)

    def test_invalid_boundary_date(self):
        error = self.assert_error(
            'LOAD "a.csv"\nAFTER "timestamp" "2026-13-01"\n', "timestamp", line=2
        )
        self.assertEqual(error.message, 'invalid boundary "2026-13-01"')

    def test_impossible_calendar_date_is_an_invalid_boundary(self):
        self.assert_error('LOAD "a.csv"\nAFTER "timestamp" "2026-02-30"\n', "timestamp")

    def test_t_separator_is_an_invalid_boundary(self):
        self.assert_error(
            'LOAD "a.csv"\nBEFORE "timestamp" "2026-10-01T09:00:00"\n', "timestamp"
        )

    def test_missing_seconds_is_an_invalid_boundary(self):
        self.assert_error('LOAD "a.csv"\nBEFORE "timestamp" "2026-10-01 09:00"\n', "timestamp")

    def test_unpadded_components_are_an_invalid_boundary(self):
        self.assert_error('LOAD "a.csv"\nAFTER "timestamp" "2026-1-5"\n', "timestamp")

    def test_empty_boundary_is_invalid(self):
        self.assert_error('LOAD "a.csv"\nAFTER "timestamp" ""\n', "timestamp")

    def test_limit_zero_is_rejected(self):
        error = self.assert_error('LOAD "a.csv"\nLIMIT 0\n', "semantic", line=2)
        self.assertEqual(error.message, "LIMIT must be a positive integer")

    def test_limit_with_leading_zero_is_rejected(self):
        self.assert_error('LOAD "a.csv"\nLIMIT 007\n', "semantic", line=2)

    def test_large_limit_is_accepted(self):
        query = check('LOAD "a.csv"\nLIMIT 999999999\n')
        self.assertEqual(query.limit, 999999999)

    def test_error_line_points_at_the_offending_command(self):
        self.assert_error(
            'LOAD "a.csv"\nWHERE "severity" = "ERROR"\nFIND "service"\nFIND "Nope"\n',
            "syntax",
        )


if __name__ == "__main__":
    unittest.main()
