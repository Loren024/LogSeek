"""Parser tests covering the spec §12.7 parser cases."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from logseek import LogSeekError, lex, parse


def build(text):
    """Parse query text into a Query object."""
    return parse(lex(text))


class ParserStructureTests(unittest.TestCase):
    def test_load_only(self):
        query = build('LOAD "application_logs.csv"')
        self.assertEqual(query.source_path, "application_logs.csv")
        self.assertEqual(query.where_conditions, [])
        self.assertIsNone(query.output_columns)
        self.assertIsNone(query.sort_column)
        self.assertIsNone(query.limit)
        self.assertIsNone(query.export_path)

    def test_single_line_without_trailing_newline(self):
        query = build('LOAD "application_logs.csv"')
        self.assertEqual(query.source_path, "application_logs.csv")

    def test_every_command_in_permitted_order(self):
        query = build(
            'LOAD "a.csv"\n'
            'WHERE "severity" = "ERROR"\n'
            'AFTER "timestamp" "2026-10-01"\n'
            'BEFORE "timestamp" "2026-11-01"\n'
            'FIND "service", "message"\n'
            'ORDER BY "timestamp" DESC\n'
            "LIMIT 5\n"
            'EXPORT "out.csv"\n'
        )
        self.assertEqual(len(query.where_conditions), 1)
        self.assertEqual(len(query.after_boundaries), 1)
        self.assertEqual(len(query.before_boundaries), 1)
        self.assertEqual(query.output_columns, ["service", "message"])
        self.assertEqual(query.sort_column, "timestamp")
        self.assertEqual(query.sort_direction, "DESC")
        self.assertEqual(query.limit, 5)
        self.assertEqual(query.export_path, "out.csv")

    def test_repeated_filters_are_stored_in_order(self):
        query = build(
            'LOAD "a.csv"\n'
            'WHERE "severity" = "ERROR"\n'
            'WHERE "service" = "auth"\n'
            'AFTER "timestamp" "2026-10-01"\n'
            'AFTER "timestamp" "2026-10-02"\n'
            'BEFORE "timestamp" "2026-11-01"\n'
            'BEFORE "timestamp" "2026-12-01"\n'
        )
        self.assertEqual(len(query.where_conditions), 2)
        self.assertEqual([b.value for b in query.after_boundaries], ["2026-10-01", "2026-10-02"])
        self.assertEqual([b.value for b in query.before_boundaries], ["2026-11-01", "2026-12-01"])

    def test_bare_find_means_all_columns(self):
        query = build('LOAD "a.csv"\nFIND\n')
        self.assertIsNone(query.output_columns)

    def test_omitted_find_means_all_columns(self):
        query = build('LOAD "a.csv"\n')
        self.assertIsNone(query.output_columns)

    def test_order_by_defaults_to_ascending(self):
        query = build('LOAD "a.csv"\nORDER BY "timestamp"\n')
        self.assertEqual(query.sort_direction, "ASC")

    def test_blank_lines_are_ignored(self):
        query = build('LOAD "a.csv"\n\n\nWHERE "severity" = "ERROR"\n\n')
        self.assertEqual(len(query.where_conditions), 1)

    def test_trailing_whitespace_on_a_command_line_is_legal(self):
        query = build('LOAD "a.csv"   \t\nLIMIT 3   \n')
        self.assertEqual(query.limit, 3)

    def test_whitespace_only_line_is_a_blank_line(self):
        query = build('LOAD "a.csv"\n   \t  \nLIMIT 3\n')
        self.assertEqual(query.limit, 3)

    def test_quoted_values_keep_spaces_and_escapes(self):
        query = build('LOAD "a.csv"\nWHERE "message" = "say \\"hi\\" now"')
        condition = query.where_conditions[0].condition
        self.assertEqual(condition.value, 'say "hi" now')

    def test_keywords_are_case_insensitive(self):
        query = build('load "a.csv"\nwhere "severity" = "ERROR"\norder by "timestamp" desc\n')
        self.assertEqual(query.where_conditions[0].condition.operator, "=")
        self.assertEqual(query.sort_direction, "DESC")


class ParserPrecedenceTests(unittest.TestCase):
    def test_and_binds_tighter_than_or(self):
        query = build(
            'LOAD "a.csv"\n'
            'WHERE "severity" = "ERROR" OR "severity" = "WARNING" AND "service" = "payments"'
        )
        root = query.where_conditions[0]
        self.assertEqual(root.kind, "OR")
        self.assertEqual(root.left.kind, "COND")
        self.assertEqual(root.right.kind, "AND")

    def test_parentheses_override_precedence(self):
        query = build(
            'LOAD "a.csv"\n'
            'WHERE ("severity" = "ERROR" OR "severity" = "WARNING") AND "service" = "payments"'
        )
        root = query.where_conditions[0]
        self.assertEqual(root.kind, "AND")
        self.assertEqual(root.left.kind, "OR")
        self.assertEqual(root.right.kind, "COND")

    def test_nested_parentheses(self):
        query = build(
            'LOAD "a.csv"\n'
            'WHERE (("severity" = "ERROR") AND ("service" = "auth" OR "service" = "payments"))'
        )
        root = query.where_conditions[0]
        self.assertEqual(root.kind, "AND")
        self.assertEqual(root.right.kind, "OR")

    def test_contains_operator(self):
        query = build('LOAD "a.csv"\nWHERE "message" CONTAINS "retry"')
        self.assertEqual(query.where_conditions[0].condition.operator, "CONTAINS")

    def test_not_equals_operator(self):
        query = build('LOAD "a.csv"\nWHERE "severity" != "INFO"')
        self.assertEqual(query.where_conditions[0].condition.operator, "!=")


class ParserErrorTests(unittest.TestCase):
    def assert_syntax_error(self, text, line=None):
        with self.assertRaises(LogSeekError) as caught:
            build(text)
        self.assertEqual(caught.exception.category, "syntax")
        if line is not None:
            self.assertEqual(caught.exception.line, line)

    def test_empty_query(self):
        self.assert_syntax_error("\n\n")

    def test_load_must_be_first(self):
        self.assert_syntax_error('WHERE "severity" = "ERROR"\n')

    def test_load_may_appear_only_once(self):
        self.assert_syntax_error('LOAD "a.csv"\nLOAD "b.csv"\n')

    def test_repeated_filters_in_invalid_order(self):
        self.assert_syntax_error(
            'LOAD "a.csv"\nAFTER "timestamp" "2026-10-01"\nWHERE "severity" = "ERROR"\n'
        )

    def test_find_after_order_by_is_invalid(self):
        self.assert_syntax_error('LOAD "a.csv"\nORDER BY "timestamp"\nFIND "service"\n')

    def test_unknown_command(self):
        self.assert_syntax_error('LOAD "a.csv"\nSELECT "service"\n')

    def test_export_must_be_last(self):
        self.assert_syntax_error('LOAD "a.csv"\nEXPORT "out.csv"\nLIMIT 1\n')

    def test_export_may_appear_only_once(self):
        self.assert_syntax_error('LOAD "a.csv"\nEXPORT "a.csv"\nEXPORT "b.csv"\n')

    def test_missing_condition_value(self):
        self.assert_syntax_error('LOAD "a.csv"\nWHERE "severity" =\n')

    def test_empty_parentheses(self):
        self.assert_syntax_error('LOAD "a.csv"\nWHERE ()\n')

    def test_unmatched_parenthesis(self):
        self.assert_syntax_error('LOAD "a.csv"\nWHERE ("severity" = "ERROR"\n')

    def test_extra_tokens_after_condition(self):
        self.assert_syntax_error('LOAD "a.csv"\nWHERE "severity" = "ERROR" "service"\n')

    def test_unsupported_operator(self):
        self.assert_syntax_error('LOAD "a.csv"\nWHERE "severity" LIKE "ERROR"\n')

    def test_missing_operator(self):
        self.assert_syntax_error('LOAD "a.csv"\nWHERE "severity" "ERROR"\n')

    def test_load_without_a_path(self):
        self.assert_syntax_error("LOAD\n")

    def test_load_with_extra_tokens(self):
        self.assert_syntax_error('LOAD "a.csv" "b.csv"\n')

    def test_order_without_by(self):
        self.assert_syntax_error('LOAD "a.csv"\nORDER "timestamp"\n')

    def test_order_by_without_a_column(self):
        self.assert_syntax_error('LOAD "a.csv"\nORDER BY\n')

    def test_invalid_sort_direction(self):
        self.assert_syntax_error('LOAD "a.csv"\nORDER BY "timestamp" SIDEWAYS\n')

    def test_find_without_quotes(self):
        self.assert_syntax_error('LOAD "a.csv"\nFIND service\n')

    def test_find_trailing_comma(self):
        self.assert_syntax_error('LOAD "a.csv"\nFIND "service",\n')

    def test_limit_without_a_number(self):
        self.assert_syntax_error('LOAD "a.csv"\nLIMIT many\n')

    def test_limit_with_extra_tokens(self):
        self.assert_syntax_error('LOAD "a.csv"\nLIMIT 1 2\n')

    def test_after_without_a_boundary(self):
        self.assert_syntax_error('LOAD "a.csv"\nAFTER "timestamp"\n')

    def test_error_reports_the_offending_line(self):
        self.assert_syntax_error(
            'LOAD "a.csv"\nWHERE "severity" = "ERROR"\nFIND "service"\nWHERE "a" = "b"\n',
            line=4,
        )


class ParserEquivalenceTests(unittest.TestCase):
    def test_file_and_stdin_text_parse_identically(self):
        query_path = os.path.join(REPO_ROOT, "tests", "queries", "14_full_pipeline.lsq")
        with open(query_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(build(text), build(text.strip() + "\n"))


if __name__ == "__main__":
    unittest.main()