"""Export tests: byte-level output, overwrite protection, and prompt behaviour."""

import contextlib
import io
import os
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
from logseek import ExportCancelled, LogSeekError, Result, export_csv


def make_result(columns, rows):
    """Build a Result for export."""
    return Result(list(columns), [list(row) for row in rows], 0, False)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.destination = os.path.join(self._tempdir.name, "out.csv")
        # A temporary "source" CSV: no test ever points EXPORT at the sample dataset.
        self.source = support.write_csv(
            self._tempdir.name, "source.csv", support.HEADER_ONLY_TEXT
        )

    def read_bytes(self, path):
        """Return the raw bytes of a file."""
        with open(path, "rb") as handle:
            return handle.read()

    def test_writes_crlf_and_utf8_without_bom(self):
        result = make_result(["Service", "message"], [["auth", "x"], ["payments", "y"]])
        export_csv(self.destination, result, self.source)
        self.assertEqual(
            self.read_bytes(self.destination),
            b"Service,message\r\n" + b"auth,x\r\n" + b"payments,y\r\n",
        )

    def test_zero_rows_writes_a_header_only_file(self):
        result = make_result(["Service", "message"], [])
        export_csv(self.destination, result, self.source)
        self.assertEqual(self.read_bytes(self.destination), b"Service,message\r\n")

    def test_rfc4180_quoting_is_applied(self):
        result = make_result(["message"], [["retry, limit"], ['said "hi"']])
        export_csv(self.destination, result, self.source)
        self.assertEqual(
            self.read_bytes(self.destination),
            b'message\r\n"retry, limit"\r\n"said ""hi"""\r\n',
        )

    def test_embedded_line_breaks_are_quoted(self):
        result = make_result(["message"], [["line one\r\nline two"]])
        export_csv(self.destination, result, self.source)
        self.assertEqual(
            self.read_bytes(self.destination), b'message\r\n"line one\r\nline two"\r\n'
        )

    def test_long_values_are_written_in_full(self):
        value = "z" * 60
        result = make_result(["message"], [[value]])
        export_csv(self.destination, result, self.source)
        self.assertIn(value.encode("utf-8"), self.read_bytes(self.destination))

    def test_source_file_cannot_be_overwritten(self):
        with self.assertRaises(LogSeekError) as caught:
            export_csv(self.source, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(caught.exception.category, "export")
        self.assertEqual(caught.exception.message, "refusing to overwrite the source CSV")

    def test_source_file_cannot_be_overwritten_via_another_spelling(self):
        alternate = os.path.join(self._tempdir.name, ".", "source.csv")
        with self.assertRaises(LogSeekError) as caught:
            export_csv(alternate, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(caught.exception.category, "export")

    def test_uppercase_extension_is_rejected(self):
        with self.assertRaises(LogSeekError) as caught:
            export_csv(
                os.path.join(self._tempdir.name, "OUT.CSV"),
                make_result(["Service"], [["auth"]]),
                self.source,
            )
        self.assertEqual(caught.exception.category, "export")
        self.assertIn("lowercase .csv extension", caught.exception.message)

    def test_missing_destination_directory_is_an_export_error(self):
        target = os.path.join(self._tempdir.name, "missing", "out.csv")
        with self.assertRaises(LogSeekError) as caught:
            export_csv(target, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(caught.exception.category, "export")
        self.assertIn("does not exist", caught.exception.message)
        self.assertFalse(os.path.exists(os.path.dirname(target)))

    def test_new_destination_needs_no_confirmation(self):
        with mock.patch.object(logseek, "_open_interactive_input", return_value=(None, False)):
            export_csv(self.destination, make_result(["Service"], [["auth"]]), self.source)
        self.assertTrue(os.path.exists(self.destination))

    def test_declining_leaves_the_existing_file_unchanged(self):
        original = b"keep me exactly as I am\r\n"
        with open(self.destination, "wb") as handle:
            handle.write(original)
        with mock.patch.object(logseek, "_read_overwrite_answer", return_value="n\n"):
            with self.assertRaises(ExportCancelled):
                export_csv(self.destination, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(self.read_bytes(self.destination), original)

    def test_accepting_overwrites_the_file(self):
        with open(self.destination, "wb") as handle:
            handle.write(b"old\r\n")
        with mock.patch.object(logseek, "_read_overwrite_answer", return_value="Y\n"):
            export_csv(self.destination, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(self.read_bytes(self.destination), b"Service\r\nauth\r\n")

    def test_empty_answer_declines(self):
        with open(self.destination, "wb") as handle:
            handle.write(b"old\r\n")
        with mock.patch.object(logseek, "_read_overwrite_answer", return_value=""):
            with self.assertRaises(ExportCancelled):
                export_csv(self.destination, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(self.read_bytes(self.destination), b"old\r\n")

    def test_prompt_is_written_to_stderr_when_input_is_interactive(self):
        with open(self.destination, "wb") as handle:
            handle.write(b"old\r\n")
        stderr = io.StringIO()
        with mock.patch.object(
            logseek, "_open_interactive_input", return_value=(io.StringIO("y\n"), False)
        ):
            with contextlib.redirect_stderr(stderr):
                export_csv(self.destination, make_result(["Service"], [["auth"]]), self.source)
        self.assertIn("already exists. Overwrite? [y/N] ", stderr.getvalue())

    def test_no_prompt_is_shown_when_input_is_not_interactive(self):
        with open(self.destination, "wb") as handle:
            handle.write(b"old\r\n")
        stderr = io.StringIO()
        with mock.patch.object(logseek, "_open_interactive_input", return_value=(None, False)):
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(ExportCancelled):
                    export_csv(self.destination, make_result(["Service"], [["auth"]]), self.source)
        self.assertEqual(stderr.getvalue(), "")

    def test_export_preserves_column_and_row_order(self):
        result = make_result(["message", "Service"], [["first", "b"], ["second", "a"]])
        export_csv(self.destination, result, self.source)
        self.assertEqual(
            self.read_bytes(self.destination),
            b"message,Service\r\nfirst,b\r\nsecond,a\r\n",
        )
    def test_header_only_source_exports_a_header_only_file(self):
        export_csv(self.destination, make_result(["Service"], []), self.source)
        self.assertEqual(self.read_bytes(self.destination), b"Service\r\n")

    def test_quoted_source_values_round_trip_through_export(self):
        source = support.write_csv(
            self._tempdir.name, "quoted.csv", support.QUOTED_TEXT
        )
        table = logseek.load_csv(source)
        result = make_result(["message"], [[row[4]] for row in table.rows])
        export_csv(self.destination, result, source)
        self.assertEqual(
            self.read_bytes(self.destination),
            b"message\r\n"
            b'"retry, limit exceeded"\r\n'
            b'"said ""connection failed"" twice"\r\n'
            b'"line one\nline two"\r\n',
        )




if __name__ == "__main__":
    unittest.main()