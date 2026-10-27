"""CLI tests: exit codes, output streams, canonical strings, and export flow."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (REPO_ROOT, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support
import logseek

QUERIES = support.QUERIES
SAMPLE = support.sample_path()

# A small CSV for CLI checks that do not need the 10,000-row dataset.
SMALL_TEXT = (
    support.HEADER + "\n"
    + "2026-10-01 09:15:00,payments,4021,ERROR,retry limit exceeded\n"
    + "2026-10-01 08:44:02,auth,1180,ERROR,token expired\n"
    + "2026-09-30 23:59:59,auth,1180,WARNING,token expiring soon\n"
)


def run_cli(args, stdin_text=None):
    """Run logseek.py in a subprocess from the repository root."""
    return subprocess.run(
        [sys.executable, "logseek.py", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )


class CliSuccessTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def write_query(self, text, name="query.lsq"):
        """Write query text to a temporary .lsq file and return its path."""
        path = os.path.join(self._tempdir.name, name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def small_csv(self):
        """Write the small three-row CSV and return its path with forward slashes."""
        path = support.write_csv(self._tempdir.name, "small.csv", SMALL_TEXT)
        return path.replace(os.sep, "/")

    def test_success_exit_code_and_streams(self):
        completed = run_cli([os.path.join(QUERIES, "03_where.lsq")])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertTrue(completed.stdout.startswith("timestamp"))
        self.assertIn("retry limit exceeded", completed.stdout)

    def test_stdout_contains_data_only(self):
        path = self.write_query('LOAD "%s"\n' % self.small_csv())
        completed = run_cli([path])
        self.assertEqual(completed.returncode, 0)
        self.assertNotIn("logseek:", completed.stdout)
        self.assertNotIn("Exported", completed.stdout)

    def test_empty_result_message(self):
        path = self.write_query(
            'LOAD "%s"\nWHERE "Service" = "nobody"\n' % self.small_csv()
        )
        completed = run_cli([path])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "No matching records found.\n")

    def test_exclusion_count_is_reported_after_the_table(self):
        completed = run_cli([os.path.join(QUERIES, "04_after.lsq")])
        # The committed dataset holds exactly 78 malformed timestamps.
        self.assertTrue(
            completed.stdout.endswith("\n78 rows excluded: malformed timestamp.\n")
        )
        self.assertIn("\n\n78 rows excluded", completed.stdout)

    def test_singular_exclusion_count(self):
        data = (
            support.HEADER + "\n"
            + "2026-10-01 09:00:00,auth,1,INFO,good\n"
            + "broken,auth,2,INFO,bad\n"
        )
        csv_path = support.write_csv(self._tempdir.name, "one_bad.csv", data)
        query = self.write_query(
            'LOAD "%s"\nAFTER "timestamp" "2026-01-01"\n'
            % csv_path.replace(os.sep, "/")
        )
        completed = run_cli([query])
        self.assertIn("1 row excluded: malformed timestamp.", completed.stdout)

    def test_zero_exclusions_are_still_reported(self):
        query = self.write_query(
            'LOAD "%s"\nAFTER "timestamp" "2026-01-01"\n' % self.small_csv()
        )
        completed = run_cli([query])
        self.assertIn("0 rows excluded: malformed timestamp.", completed.stdout)

    def test_no_exclusion_count_without_a_timestamp_filter(self):
        completed = run_cli([os.path.join(QUERIES, "03_where.lsq")])
        self.assertNotIn("excluded", completed.stdout)

    def test_query_from_stdin_matches_query_from_file(self):
        query_path = os.path.join(QUERIES, "03_where.lsq")
        with open(query_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        from_file = run_cli([query_path])
        from_stdin = run_cli([], stdin_text=text)
        self.assertEqual(from_stdin.returncode, 0)
        self.assertEqual(from_stdin.stdout, from_file.stdout)
        self.assertEqual(from_stdin.stderr, "")

    def test_two_runs_produce_identical_stdout(self):
        first = run_cli([os.path.join(QUERIES, "07_limit.lsq")])
        second = run_cli([os.path.join(QUERIES, "07_limit.lsq")])
        self.assertEqual(first.stdout, second.stdout)


class CliErrorTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def write_query(self, text, name="query.lsq"):
        """Write query text to a temporary .lsq file and return its path."""
        path = os.path.join(self._tempdir.name, name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def assert_error(self, completed, code, prefix, expect_empty_stdout=True):
        self.assertEqual(completed.returncode, code)
        self.assertTrue(completed.stderr.startswith(prefix), completed.stderr)
        if expect_empty_stdout:
            self.assertEqual(completed.stdout, "")

    def test_two_arguments_is_an_invocation_error(self):
        completed = run_cli(["a.lsq", "b.lsq"])
        self.assert_error(
            completed, 2, "logseek: error: invocation: expected exactly one query file argument"
        )

    def test_missing_query_file_is_an_io_error(self):
        completed = run_cli([os.path.join(self._tempdir.name, "nope.lsq")])
        self.assert_error(completed, 1, "logseek: error: io:")

    def test_lexical_error(self):
        path = self.write_query('LOAD "unterminated\n')
        completed = run_cli([path])
        self.assert_error(completed, 1, "logseek: error: lexical:")

    def test_syntax_error_reports_a_line_number(self):
        path = self.write_query('LOAD "%s"\nSELECT "Service"\n' % SAMPLE)
        completed = run_cli([path])
        self.assert_error(completed, 1, "logseek: error: syntax:")
        self.assertIn("(line 2)", completed.stderr)

    def test_semantic_error_message_shape(self):
        path = self.write_query('LOAD "%s"\nWHERE "Severity" = "ERROR"\n' % SAMPLE)
        completed = run_cli([path])
        self.assert_error(
            completed, 1, 'logseek: error: semantic: unknown column "Severity" (line 2)'
        )

    def test_timestamp_error(self):
        path = self.write_query('LOAD "%s"\nAFTER "timestamp" "2026-13-01"\n' % SAMPLE)
        completed = run_cli([path])
        self.assert_error(
            completed, 1, 'logseek: error: timestamp: invalid boundary "2026-13-01" (line 2)'
        )

    def test_input_error_for_a_missing_csv(self):
        path = self.write_query('LOAD "missing_file.csv"\n')
        completed = run_cli([path])
        self.assert_error(completed, 1, "logseek: error: input:")

    def test_input_error_for_a_bad_header(self):
        target = support.write_csv(
            self._tempdir.name, "bad_header.csv", support.BAD_HEADER_TEXT
        ).replace(os.sep, "/")
        path = self.write_query('LOAD "%s"\n' % target)
        completed = run_cli([path])
        self.assert_error(completed, 1, "logseek: error: input: expected header '")

    def test_empty_stdin_is_a_syntax_error(self):
        completed = run_cli([], stdin_text="")
        self.assert_error(completed, 1, "logseek: error: syntax:")

    def test_export_error_for_a_missing_directory(self):
        # Export runs after the table is displayed (brief §6), so stdout is not empty.
        target = os.path.join(self._tempdir.name, "missing", "out.csv").replace(os.sep, "/")
        path = self.write_query('LOAD "%s"\nEXPORT "%s"\n' % (SAMPLE, target))
        completed = run_cli([path])
        self.assert_error(completed, 1, "logseek: error: export:", expect_empty_stdout=False)
        self.assertTrue(completed.stdout.startswith("timestamp"))


class CliExportTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.destination = os.path.join(self._tempdir.name, "out.csv")

    def write_query(self, text, name="query.lsq"):
        """Write query text to a temporary .lsq file and return its path."""
        path = os.path.join(self._tempdir.name, name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def small_csv(self):
        """Write the small three-row CSV and return its path with forward slashes."""
        path = support.write_csv(self._tempdir.name, "small.csv", SMALL_TEXT)
        return path.replace(os.sep, "/")

    def test_export_succeeds_and_confirms_on_stderr(self):
        target = self.destination.replace(os.sep, "/")
        path = self.write_query('LOAD "%s"\nEXPORT "%s"\n' % (self.small_csv(), target))
        completed = run_cli([path])
        self.assertEqual(completed.returncode, 0)
        self.assertIn("Exported 3 rows to", completed.stderr)
        self.assertNotIn("Exported", completed.stdout)
        data = support.read_bytes(self.destination)
        self.assertTrue(data.startswith(b"timestamp,Service,event_code,severity,message\r\n"))

    def test_zero_match_export_writes_a_header_only_file(self):
        target = self.destination.replace(os.sep, "/")
        path = self.write_query(
            'LOAD "%s"\nWHERE "Service" = "nobody"\nFIND "Service", "message"\n'
            'EXPORT "%s"\n' % (self.small_csv(), target)
        )
        completed = run_cli([path])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(support.read_bytes(self.destination), b"Service,message\r\n")

    def test_declined_overwrite_leaves_the_file_unchanged(self):
        original = b"untouched\r\n"
        with open(self.destination, "wb") as handle:
            handle.write(original)
        target = self.destination.replace(os.sep, "/")
        path = self.write_query('LOAD "%s"\nEXPORT "%s"\n' % (self.small_csv(), target))
        completed = run_cli([path], stdin_text="")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr.strip(), "Export cancelled.")
        self.assertEqual(support.read_bytes(self.destination), original)

    def test_source_csv_cannot_be_overwritten(self):
        # A temporary source, so no test ever points EXPORT at the sample dataset.
        source = self.small_csv()
        path = self.write_query('LOAD "%s"\nEXPORT "%s"\n' % (source, source))
        completed = run_cli([path])
        self.assertEqual(completed.returncode, 1)
        self.assertTrue(
            completed.stderr.startswith("logseek: error: export: refusing to overwrite")
        )


class InvocationTests(unittest.TestCase):
    def test_no_arguments_with_interactive_stdin_is_an_invocation_error(self):
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", fake_stdin):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = logseek.main([])
        self.assertEqual(code, 2)
        self.assertEqual(
            err.getvalue(),
            "logseek: error: invocation: expected exactly one query file argument\n",
        )
        self.assertEqual(out.getvalue(), "")

    def test_main_returns_zero_for_a_valid_query(self):
        query_path = os.path.join(QUERIES, "03_where.lsq")
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = logseek.main([query_path])
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertTrue(out.getvalue().startswith("timestamp"))


if __name__ == "__main__":
    unittest.main()