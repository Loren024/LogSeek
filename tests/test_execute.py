"""Executor tests: the 14 required test units plus filtering and sorting rules."""

import os
import sys
import tempfile
import unittest
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (REPO_ROOT, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support
from logseek import (
    SCHEMA_HEADERS,
    CsvTable,
    execute,
    lex,
    load_csv,
    parse,
    validate_semantics,
)

QUERIES = support.QUERIES


def table_from(rows):
    """Build a CsvTable from raw data rows."""
    return CsvTable(list(SCHEMA_HEADERS), [list(row) for row in rows])


def run_text(text, table):
    """Parse, validate, and execute query text against a table."""
    query = parse(lex(text))
    validate_semantics(query)
    return execute(query, table)


def parseable(stamp):
    """Return True when a stored timestamp uses one of the two accepted formats."""
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(stamp, pattern)
            return True
        except ValueError:
            continue
    return False


class RequiredUnitTests(unittest.TestCase):
    """The seven single-command and seven combination units from spec §10."""

    @classmethod
    def setUpClass(cls):
        cls._cwd = os.getcwd()
        os.chdir(REPO_ROOT)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._cwd)

    def run_query_file(self, name):
        """Run one stored query file through the normal pipeline."""
        with open(os.path.join(QUERIES, name), "r", encoding="utf-8") as handle:
            text = handle.read()
        query = parse(lex(text))
        validate_semantics(query)
        return execute(query, load_csv(query.source_path))

    def test_unit_01_load(self):
        result = self.run_query_file("01_load.lsq")
        self.assertEqual(result.columns, list(SCHEMA_HEADERS))
        # 10,000 data rows, counted directly from the committed dataset (§10.1).
        self.assertEqual(len(result.rows), support.SAMPLE_ROW_COUNT)
        self.assertEqual(result.rows[0][0], "2026-09-01 00:00:00")
        self.assertEqual(result.rows[0][1], "payments")
        self.assertFalse(result.timestamp_filtered)
        self.assertEqual(result.excluded_count, 0)

    def test_unit_02_find(self):
        result = self.run_query_file("02_find.lsq")
        self.assertEqual(result.columns, ["service", "message"])
        self.assertEqual(len(result.rows), support.SAMPLE_ROW_COUNT)
        self.assertEqual(result.rows[0], ["payments", "connection reset by peer"])

    def test_unit_03_where(self):
        result = self.run_query_file("03_where.lsq")
        # `WHERE "severity" = "ERROR"` matches 2,568 rows in the committed dataset.
        self.assertEqual(len(result.rows), 2568)
        self.assertEqual({row[3] for row in result.rows}, {"ERROR"})
        self.assertEqual(result.rows[0][0], "2026-09-01 02:29:18")

    def test_unit_04_after(self):
        result = self.run_query_file("04_after.lsq")
        self.assertTrue(result.timestamp_filtered)
        # Only the final row of the dataset falls after 2027-03-29 03:00:00.
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(
            result.rows[0],
            [
                "2027-03-29 03:24:10",
                "gateway",
                "8302",
                "DEBUG",
                "connection reset by peer",
            ],
        )
        # All 78 malformed timestamps in the dataset are excluded (§2.2).
        self.assertEqual(result.excluded_count, 78)

    def test_unit_05_before(self):
        result = self.run_query_file("05_before.lsq")
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.excluded_count, 78)
        self.assertEqual(
            [row[0] for row in result.rows],
            ["2026-09-01 00:00:00", "2026-09-01 00:16:01", "2026-09-01 00:21:52"],
        )

    def test_unit_06_order_by(self):
        result = self.run_query_file("06_order_by.lsq")
        self.assertEqual(len(result.rows), support.SAMPLE_ROW_COUNT)
        stamps = [row[0] for row in result.rows]
        # Newest first: the maximum timestamp present in the committed dataset.
        self.assertEqual(stamps[0], "2027-03-29 03:24:10")
        valid = [stamp for stamp in stamps if parseable(stamp)]
        self.assertEqual(valid, sorted(valid, reverse=True))
        # Rows whose timestamp cannot be parsed are kept, after the sortable ones.
        self.assertEqual(stamps[-1], "2026-10-01T09:15:00")

    def test_unit_07_limit(self):
        result = self.run_query_file("07_limit.lsq")
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(
            result.rows,
            [
                ["2026-09-01 00:00:00", "payments", "4021", "INFO", "connection reset by peer"],
                ["2026-09-01 00:16:01", "auth", "1181", "INFO", "disk usage above threshold"],
                ["2026-09-01 00:21:52", "gateway", "8300", "DEBUG", "connection reset by peer"],
            ],
        )

    def test_unit_08_find_where(self):
        result = self.run_query_file("08_find_where.lsq")
        self.assertEqual(result.columns, ["timestamp", "event_code"])
        # `service = "gateway" AND message = "cache miss"` matches 76 rows.
        self.assertEqual(len(result.rows), 76)
        self.assertEqual(result.rows[0], ["2026-09-01 03:10:29", "8302"])
        self.assertEqual(result.rows[-1], ["2027-03-26 08:31:16", "8300"])

    def test_unit_09_where_after(self):
        result = self.run_query_file("09_where_after.lsq")
        self.assertTrue(result.timestamp_filtered)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(
            result.rows[0],
            [
                "2027-03-29 02:04:05",
                "auth",
                "1180",
                "ERROR",
                "user logged out",
            ],
        )
        # WHERE runs before timestamp filtering, so only the ERROR rows with
        # unparseable timestamps are counted here, not all 78 (§12.5, step 5).
        self.assertEqual(result.excluded_count, 18)

    def test_unit_10_where_order_limit(self):
        result = self.run_query_file("10_where_order_limit.lsq")
        # The two newest ERROR rows in the committed dataset.
        self.assertEqual(
            [row[0] for row in result.rows],
            ["2027-03-29 02:04:05", "2027-03-28 23:41:28"],
        )
        self.assertEqual(result.excluded_count, 0)

    def test_unit_11_find_where_order(self):
        result = self.run_query_file("11_find_where_order.lsq")
        self.assertEqual(result.columns, ["event_code"])
        # `service = "payments" AND event_code = "4021"` matches 551 rows.
        self.assertEqual(len(result.rows), 551)
        self.assertEqual({row[0] for row in result.rows}, {"4021"})

    def test_unit_12_after_before(self):
        result = self.run_query_file("12_after_before.lsq")
        self.assertEqual(len(result.rows), 4)
        self.assertEqual(result.excluded_count, 78)
        self.assertEqual(
            [row[0] for row in result.rows],
            [
                "2026-09-01 00:16:01",
                "2026-09-01 00:21:52",
                "2026-09-01 01:17:50",
                "2026-09-01 01:43:28",
            ],
        )

    def test_unit_13_where_before(self):
        result = self.run_query_file("13_where_before.lsq")
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(
            [row[0] for row in result.rows],
            ["2026-09-01 00:21:52", "2026-09-01 01:17:50"],
        )
        # Only the non-INFO rows with unparseable timestamps reach this stage,
        # so the count is 58 rather than the dataset-wide 78 (§12.5, step 5).
        self.assertEqual(result.excluded_count, 58)

    def test_unit_14_full_pipeline(self):
        result = self.run_query_file("14_full_pipeline.lsq")
        self.assertEqual(result.columns, ["timestamp", "service", "message"])
        self.assertEqual(
            result.rows,
            [
                ["2027-03-29 02:04:05", "auth", "user logged out"],
                ["2027-03-28 23:41:28", "notifications", 'said "connection failed" twice'],
            ],
        )
        self.assertEqual(result.excluded_count, 18)


class GeneratedFileTests(unittest.TestCase):
    """Brief §10.2 cases that need a real CSV on disk rather than an in-memory table."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def run_file(self, name, text, query_tail):
        """Write a CSV, run a query against it, and return the result."""
        path = support.write_csv(self._tempdir.name, name, text)
        query_text = 'LOAD "%s"\n%s' % (path.replace(os.sep, "/"), query_tail)
        query = parse(lex(query_text))
        validate_semantics(query)
        return execute(query, load_csv(query.source_path))

    def test_header_only_source_yields_no_rows(self):
        result = self.run_file("header_only.csv", support.HEADER_ONLY_TEXT, "")
        self.assertEqual(result.rows, [])
        self.assertEqual(result.columns, list(SCHEMA_HEADERS))

    def test_all_malformed_timestamps_are_excluded_and_counted(self):
        result = self.run_file(
            "malformed.csv",
            support.MALFORMED_TIMESTAMPS_TEXT,
            'AFTER "timestamp" "2000-01-01"\n',
        )
        self.assertEqual(result.rows, [])
        self.assertTrue(result.timestamp_filtered)
        # Every data row in the generated file has an unparseable timestamp.
        self.assertEqual(result.excluded_count, 3)


class FilteringTests(unittest.TestCase):
    def test_contains_empty_string_matches_every_row(self):
        table = table_from(
            [["2026-10-01 09:00:00", "auth", "1", "INFO", "x"], ["", "auth", "2", "INFO", "y"]]
        )
        result = run_text('LOAD "a.csv"\nWHERE "message" CONTAINS ""', table)
        self.assertEqual(len(result.rows), 2)

    def test_contains_is_case_sensitive(self):
        table = table_from([["2026-10-01 09:00:00", "auth", "1", "INFO", "Error occurred"]])
        result = run_text('LOAD "a.csv"\nWHERE "message" CONTAINS "error"', table)
        self.assertEqual(result.rows, [])

    def test_equality_is_case_sensitive(self):
        table = table_from([["2026-10-01 09:00:00", "auth", "1", "INFO", "x"]])
        result = run_text('LOAD "a.csv"\nWHERE "severity" = "info"', table)
        self.assertEqual(result.rows, [])

    def test_where_on_timestamp_compares_stored_text(self):
        table = table_from([["2026-10-01 09:00:00", "auth", "1", "INFO", "x"]])
        result = run_text('LOAD "a.csv"\nWHERE "timestamp" = "2026-10-01 09:00:00"', table)
        self.assertEqual(len(result.rows), 1)
        self.assertFalse(result.timestamp_filtered)

    def test_repeated_where_commands_narrow_the_result(self):
        table = table_from(
            [
                ["2026-10-01 09:00:00", "auth", "1", "ERROR", "x"],
                ["2026-10-01 09:00:00", "payments", "2", "ERROR", "y"],
            ]
        )
        result = run_text(
            'LOAD "a.csv"\nWHERE "severity" = "ERROR"\nWHERE "service" = "auth"', table
        )
        self.assertEqual(len(result.rows), 1)

    def test_and_or_precedence_is_applied(self):
        table = table_from(
            [
                ["2026-10-01 09:00:00", "auth", "1", "ERROR", "x"],
                ["2026-10-01 09:00:00", "payments", "2", "WARNING", "y"],
                ["2026-10-01 09:00:00", "search", "3", "WARNING", "z"],
            ]
        )
        result = run_text(
            'LOAD "a.csv"\n'
            'WHERE "severity" = "ERROR" OR "severity" = "WARNING" AND "service" = "payments"',
            table,
        )
        self.assertEqual([row[1] for row in result.rows], ["auth", "payments"])


class TimestampBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.table = table_from(
            [
                ["2026-09-30 23:59:59", "auth", "1", "INFO", "before"],
                ["2026-10-01 00:00:00", "auth", "2", "INFO", "exactly midnight"],
                ["2026-10-01 09:15:00", "auth", "3", "INFO", "same day"],
                ["2026-10-02 00:00:00", "auth", "4", "INFO", "next day"],
            ]
        )

    def test_after_is_strict_on_an_equal_instant(self):
        result = run_text('LOAD "a.csv"\nAFTER "timestamp" "2026-10-01 09:15:00"', self.table)
        self.assertEqual([row[4] for row in result.rows], ["next day"])

    def test_before_is_strict_on_an_equal_instant(self):
        result = run_text('LOAD "a.csv"\nBEFORE "timestamp" "2026-10-01 09:15:00"', self.table)
        self.assertEqual([row[4] for row in result.rows], ["before", "exactly midnight"])

    def test_date_only_boundary_is_midnight(self):
        result = run_text('LOAD "a.csv"\nAFTER "timestamp" "2026-10-01"', self.table)
        self.assertEqual([row[4] for row in result.rows], ["same day", "next day"])

    def test_before_a_date_only_boundary_excludes_that_whole_day(self):
        result = run_text('LOAD "a.csv"\nBEFORE "timestamp" "2026-10-01"', self.table)
        self.assertEqual([row[4] for row in result.rows], ["before"])

    def test_malformed_rows_are_counted_once_across_filters(self):
        table = table_from(
            [
                ["2026-10-01 09:00:00", "auth", "1", "INFO", "good"],
                ["broken", "auth", "2", "INFO", "bad one"],
                ["", "auth", "3", "INFO", "bad two"],
            ]
        )
        result = run_text(
            'LOAD "a.csv"\n'
            'AFTER "timestamp" "2026-09-01"\n'
            'AFTER "timestamp" "2026-09-02"\n'
            'BEFORE "timestamp" "2026-11-01"\n',
            table,
        )
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.excluded_count, 2)

    def test_exclusion_count_is_zero_when_no_timestamp_filter_runs(self):
        result = run_text('LOAD "a.csv"\nWHERE "severity" = "INFO"', self.table)
        self.assertFalse(result.timestamp_filtered)
        self.assertEqual(result.excluded_count, 0)


class SortingAndLimitTests(unittest.TestCase):
    def setUp(self):
        self.table = table_from(
            [
                ["2026-10-01 09:00:00", "b", "1", "INFO", "first"],
                ["2026-10-01 09:00:00", "a", "2", "INFO", "second"],
                ["2026-10-01 09:00:00", "a", "3", "INFO", "third"],
            ]
        )

    def test_text_sort_is_stable(self):
        result = run_text('LOAD "a.csv"\nORDER BY "service"', self.table)
        self.assertEqual([row[4] for row in result.rows], ["second", "third", "first"])

    def test_text_sort_descending_is_stable(self):
        result = run_text('LOAD "a.csv"\nORDER BY "service" DESC', self.table)
        self.assertEqual([row[4] for row in result.rows], ["first", "second", "third"])

    def test_sort_column_may_be_outside_the_find_columns(self):
        result = run_text('LOAD "a.csv"\nFIND "message"\nORDER BY "service"', self.table)
        self.assertEqual(result.columns, ["message"])
        self.assertEqual([row[0] for row in result.rows], ["second", "third", "first"])

    def test_limit_larger_than_matches_returns_all(self):
        result = run_text('LOAD "a.csv"\nLIMIT 99', self.table)
        self.assertEqual(len(result.rows), 3)

    def test_limit_is_applied_after_sorting(self):
        result = run_text('LOAD "a.csv"\nORDER BY "service" DESC\nLIMIT 1', self.table)
        self.assertEqual([row[4] for row in result.rows], ["first"])

    def test_find_column_order_is_preserved(self):
        result = run_text('LOAD "a.csv"\nFIND "message", "service"', self.table)
        self.assertEqual(result.columns, ["message", "service"])
        self.assertEqual(result.rows[0], ["first", "b"])

    def test_projection_does_not_modify_stored_values(self):
        table = table_from([["2026-10-01 09:00:00", "a", "1", "INFO", "line\nbreak"]])
        result = run_text('LOAD "a.csv"\nFIND "message"', table)
        self.assertEqual(result.rows, [["line\nbreak"]])
        self.assertEqual(table.rows[0][4], "line\nbreak")


if __name__ == "__main__":
    unittest.main()