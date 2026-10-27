"""CSV loader tests: extension, BOM, header schema, row shape, and encoding."""

import hashlib
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (REPO_ROOT, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support
from logseek import LogSeekError, load_csv

HEADER = support.HEADER


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def write_bytes(self, name, data):
        """Write raw bytes into the temporary directory and return the path."""
        return support.write_bytes(self._tempdir.name, name, data)

    def write(self, name, text):
        """Write text into the temporary directory and return the path."""
        return support.write_csv(self._tempdir.name, name, text)

    def assert_input_error(self, path, fragment=None):
        with self.assertRaises(LogSeekError) as caught:
            load_csv(path)
        self.assertEqual(caught.exception.category, "input")
        if fragment is not None:
            self.assertIn(fragment, caught.exception.message)
        return caught.exception

    def test_sample_dataset_loads_every_row(self):
        table = load_csv(support.SAMPLE_DATA)
        self.assertEqual(
            table.headers, ["timestamp", "Service", "event_code", "severity", "message"]
        )
        # Count verified directly from the committed file (brief §10.1).
        self.assertEqual(len(table.rows), support.SAMPLE_ROW_COUNT)
        self.assertEqual(
            table.rows[0],
            ["2026-09-01 00:00:00", "payments", "4021", "INFO", "connection reset by peer"],
        )

    def test_sample_dataset_is_unmodified(self):
        digest = hashlib.sha256(support.read_bytes(support.SAMPLE_DATA)).hexdigest()
        self.assertEqual(digest, support.SAMPLE_SHA256)

    def test_header_only_file_is_valid_and_empty(self):
        table = load_csv(self.write("header_only.csv", support.HEADER_ONLY_TEXT))
        self.assertEqual(table.rows, [])

    def test_empty_file_is_an_input_error(self):
        self.assert_input_error(self.write("empty.csv", ""), "missing header row")

    def test_renamed_header_is_an_input_error(self):
        path = self.write("bad_header.csv", support.BAD_HEADER_TEXT)
        error = self.assert_input_error(path)
        self.assertEqual(error.message, "expected header '" + HEADER + "'")

    def test_ragged_row_is_an_input_error(self):
        error = self.assert_input_error(self.write("ragged.csv", support.RAGGED_TEXT))
        self.assertEqual(error.message, "row 2 has 5 expected fields, found 4")

    def test_blank_line_in_data_is_an_input_error(self):
        error = self.assert_input_error(self.write("blank.csv", support.BLANK_LINE_TEXT))
        self.assertEqual(error.message, "blank line in CSV data at row 3")

    def test_trailing_newline_is_not_a_blank_line(self):
        path = self.write(
            "trailing.csv", HEADER + "\n" + "2026-10-01 09:00:00,a,1,INFO,x\n"
        )
        table = load_csv(path)
        self.assertEqual(len(table.rows), 1)

    def test_row_numbers_count_the_header_as_row_one(self):
        path = self.write(
            "second.csv",
            HEADER + "\n" + "2026-10-01 09:00:00,a,1,INFO,x\n" + "bad,row\n",
        )
        error = self.assert_input_error(path)
        self.assertEqual(error.message, "row 3 has 5 expected fields, found 2")

    def test_uppercase_extension_is_rejected(self):
        path = self.write("upper.CSV", support.HEADER_ONLY_TEXT)
        self.assert_input_error(path, "lowercase .csv extension")

    def test_non_csv_extension_is_rejected(self):
        path = self.write("logs.txt", HEADER + "\n")
        self.assert_input_error(path, "lowercase .csv extension")

    def test_missing_file_is_an_input_error(self):
        path = os.path.join(self._tempdir.name, "does_not_exist.csv")
        self.assert_input_error(path, "file not found")

    def test_directory_is_an_input_error(self):
        path = os.path.join(self._tempdir.name, "folder.csv")
        os.mkdir(path)
        self.assert_input_error(path, "is a directory")

    def test_bom_is_stripped_before_header_validation(self):
        table = load_csv(self.write("bom.csv", support.BOM_TEXT))
        self.assertEqual(
            table.headers, ["timestamp", "Service", "event_code", "severity", "message"]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0][4], "bom row")

    def test_undecodable_bytes_are_an_input_error(self):
        path = self.write_bytes("bad_encoding.csv", support.UNDECODABLE_BYTES)
        self.assert_input_error(path, "not valid UTF-8")

    def test_rfc4180_quoting_is_decoded(self):
        table = load_csv(self.write("quoted.csv", support.QUOTED_TEXT))
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(table.rows[0][4], "retry, limit exceeded")
        self.assertEqual(table.rows[1][4], 'said "connection failed" twice')
        self.assertEqual(table.rows[2][4], "line one\nline two")

    def test_quoted_field_with_crlf_is_decoded(self):
        data = (
            HEADER + "\r\n"
            "2026-10-01 09:00:00,a,1,INFO,\"line one\r\nline two\"\r\n"
        )
        table = load_csv(self.write("crlf_quoted.csv", data))
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0][4], "line one\r\nline two")

    def test_mixed_line_endings_are_accepted(self):
        data = (
            HEADER + "\r\n"
            "2026-10-01 09:00:00,a,1,INFO,crlf row\r\n"
            "2026-10-01 09:01:00,a,2,INFO,lf row\n"
            "2026-10-01 09:02:00,a,3,INFO,last row\r\n"
        )
        table = load_csv(self.write("mixed.csv", data))
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(table.rows[1][4], "lf row")

    def test_loader_does_not_modify_stored_values(self):
        table = load_csv(self.write("malformed.csv", support.MALFORMED_TIMESTAMPS_TEXT))
        self.assertEqual(table.rows[0][0], "2026-10-01T09:15:00")
        self.assertEqual(table.rows[2][0], "2026-02-30 10:00:00")


if __name__ == "__main__":
    unittest.main()
