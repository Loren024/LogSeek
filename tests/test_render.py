"""Renderer tests for the fixed terminal table format (spec §6.1)."""

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
from logseek import Result, load_csv, render


def make_result(columns, rows, excluded=0, filtered=False):
    """Build a Result from raw columns and rows."""
    return Result(list(columns), [list(row) for row in rows], excluded, filtered)


class RenderTests(unittest.TestCase):
    def test_empty_result_uses_the_empty_message(self):
        text = render(make_result(["severity"], []))
        self.assertEqual(text, "No matching records found.\n")

    def test_header_separator_and_row_layout(self):
        text = render(make_result(["severity", "message"], [["ERROR", "retry limit exceeded"]]))
        lines = text.split("\n")
        self.assertEqual(lines[0], "severity  message")
        self.assertEqual(lines[1], "--------  --------------------")
        self.assertEqual(lines[2], "ERROR     retry limit exceeded")
        self.assertEqual(lines[3], "")

    def test_column_width_is_the_longest_displayed_value(self):
        text = render(make_result(["Service"], [["auth"], ["payments"]]))
        lines = text.split("\n")
        self.assertEqual(lines[1], "-" * 8)
        self.assertEqual(lines[2], "auth")

    def test_columns_are_separated_by_two_spaces(self):
        text = render(make_result(["a", "b"], [["1", "2"]]))
        self.assertIn("a  b", text)

    def test_lines_are_right_trimmed(self):
        text = render(make_result(["Service", "message"], [["auth", "x"]]))
        for line in text.split("\n"):
            self.assertEqual(line, line.rstrip())

    def test_table_ends_with_exactly_one_newline(self):
        text = render(make_result(["Service"], [["auth"]]))
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_value_longer_than_the_cap_is_truncated(self):
        long_value = "x" * 41
        text = render(make_result(["message"], [[long_value]]))
        cell = text.split("\n")[2]
        self.assertEqual(len(cell), 40)
        self.assertEqual(cell, "x" * 37 + "...")

    def test_value_of_exactly_the_cap_is_not_truncated(self):
        value = "y" * 40
        text = render(make_result(["message"], [[value]]))
        self.assertEqual(text.split("\n")[2], value)

    def test_line_breaks_are_shown_as_backslash_n(self):
        text = render(make_result(["message"], [["line one\r\nline two"]]))
        self.assertIn("line one\\nline two", text)
        self.assertEqual(len(text.strip().split("\n")), 3)

    def test_lone_line_breaks_are_substituted(self):
        text = render(make_result(["message"], [["a\rb\nc"]]))
        self.assertIn("a\\nb\\nc", text)

    def test_tabs_are_shown_as_backslash_t(self):
        text = render(make_result(["message"], [["a\tb"]]))
        self.assertIn("a\\tb", text)

    def test_substitution_happens_before_measuring(self):
        value = "\n".join(["1234567890"] * 5)
        text = render(make_result(["message"], [[value]]))
        cell = text.split("\n")[2]
        self.assertEqual(len(cell), 40)
        self.assertTrue(cell.endswith("..."))

    def test_render_does_not_mutate_rows(self):
        result = make_result(["message"], [["a\nb\tc"]])
        render(result)
        self.assertEqual(result.rows, [["a\nb\tc"]])

    def test_headers_are_not_truncated(self):
        header = "a_very_long_column_name_that_exceeds_forty_characters"
        text = render(make_result([header], [["x"]]))
        lines = text.split("\n")
        self.assertEqual(lines[0], header)
        self.assertEqual(lines[1], "-" * len(header))

    def test_multiple_rows_keep_input_order(self):
        text = render(make_result(["Service"], [["b"], ["a"]]))
        self.assertEqual(text.split("\n")[2:4], ["b", "a"])

class GeneratedFileRenderTests(unittest.TestCase):
    """Brief §10.2 cases rendered from a real CSV rather than an in-memory Result."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def render_file(self, name, text, columns):
        """Load a generated CSV, render the chosen columns, and return the table text."""
        path = support.write_csv(self._tempdir.name, name, text)
        table = load_csv(path)
        index = {header: position for position, header in enumerate(table.headers)}
        rows = [[row[index[column]] for column in columns] for row in table.rows]
        return render(Result(list(columns), rows, 0, False))

    def test_quoted_fields_render_on_single_lines(self):
        text = self.render_file("quoted.csv", support.QUOTED_TEXT, ["message"])
        lines = text.split("\n")
        self.assertEqual(len(lines), 6)
        self.assertIn("retry, limit exceeded", lines[2])
        self.assertIn('said "connection failed" twice', lines[3])
        # The embedded line break is substituted, so the row stays on one line.
        self.assertIn("line one\\nline two", lines[4])

    def test_wide_value_is_truncated_on_screen(self):
        text = self.render_file("wide.csv", support.WIDE_TEXT, ["message"])
        cell = text.split("\n")[2]
        self.assertEqual(len(cell), 40)
        self.assertTrue(cell.endswith("..."))


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
