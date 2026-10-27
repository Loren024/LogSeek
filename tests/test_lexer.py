"""Lexer tests: token kinds, positions, escapes, and lexical errors."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import logseek
from logseek import LogSeekError, lex


class LexerTests(unittest.TestCase):
    def test_keywords_are_case_insensitive(self):
        tokens = lex('load "x.csv"')
        self.assertEqual(tokens[0].kind, "KEYWORD")
        self.assertEqual(tokens[0].value, "LOAD")

    def test_all_keywords_are_recognized(self):
        text = "LOAD FIND WHERE AFTER BEFORE ORDER BY LIMIT EXPORT AND OR CONTAINS ASC DESC"
        kinds = [token.kind for token in lex(text)]
        self.assertEqual(kinds, ["KEYWORD"] * 14 + ["EOF"])

    def test_unknown_word_is_not_a_keyword(self):
        tokens = lex('SELECT "x"')
        self.assertEqual(tokens[0].kind, "WORD")
        self.assertEqual(tokens[0].value, "SELECT")

    def test_string_token_decodes_escapes(self):
        tokens = lex(r'LOAD "a\"b\\c\nd"')
        self.assertEqual(tokens[1].kind, "STRING")
        self.assertEqual(tokens[1].value, 'a"b\\c\\nd')

    def test_string_may_contain_spaces(self):
        tokens = lex('LOAD "root folder/my logs.csv"')
        self.assertEqual(tokens[1].value, "root folder/my logs.csv")

    def test_operators_and_punctuation(self):
        kinds = [token.kind for token in lex('WHERE ("a" != "b",)')]
        self.assertEqual(
            kinds, ["KEYWORD", "LPAREN", "STRING", "NOT_EQUALS", "STRING", "COMMA", "RPAREN", "EOF"]
        )

    def test_integer_token(self):
        tokens = lex("LIMIT 10")
        self.assertEqual(tokens[1].kind, "INTEGER")
        self.assertEqual(tokens[1].value, "10")

    def test_newline_kinds(self):
        tokens = lex('LOAD "a.csv"\r\nLOAD "b.csv"\n')
        newlines = [token.value for token in tokens if token.kind == "NEWLINE"]
        self.assertEqual(newlines, ["\r\n", "\n"])

    def test_line_and_column_tracking(self):
        tokens = lex('LOAD "a.csv"\nWHERE "severity" = "ERROR"\n')
        where = tokens[3]
        self.assertEqual(where.value, "WHERE")
        self.assertEqual(where.line, 2)
        self.assertEqual(where.col, 1)
        severity = tokens[4]
        self.assertEqual(severity.line, 2)
        self.assertEqual(severity.col, 7)

    def test_bom_is_stripped(self):
        tokens = lex('\ufeffLOAD "a.csv"')
        self.assertEqual(tokens[0].kind, "KEYWORD")
        self.assertEqual(tokens[0].value, "LOAD")

    def test_eof_token_is_last(self):
        tokens = lex('LOAD "a.csv"')
        self.assertEqual(tokens[-1].kind, "EOF")

    def test_unterminated_string_is_a_lexical_error(self):
        with self.assertRaises(LogSeekError) as caught:
            lex('LOAD "a.csv')
        self.assertEqual(caught.exception.category, "lexical")
        self.assertEqual(caught.exception.line, 1)

    def test_dangling_escape_is_a_lexical_error(self):
        with self.assertRaises(LogSeekError) as caught:
            lex('LOAD "a.csv\\')
        self.assertEqual(caught.exception.category, "lexical")

    def test_unescaped_line_break_in_string_is_a_lexical_error(self):
        with self.assertRaises(LogSeekError) as caught:
            lex('LOAD "a\nb.csv"')
        self.assertEqual(caught.exception.category, "lexical")

    def test_unexpected_character_is_a_lexical_error(self):
        with self.assertRaises(LogSeekError) as caught:
            lex('LOAD "a.csv" @')
        self.assertEqual(caught.exception.category, "lexical")
        self.assertEqual(caught.exception.line, 1)

    def test_lone_carriage_return_is_a_lexical_error(self):
        with self.assertRaises(LogSeekError) as caught:
            lex('LOAD "a.csv"\rLOAD "b.csv"')
        self.assertEqual(caught.exception.category, "lexical")

    def test_comment_marker_is_rejected(self):
        with self.assertRaises(LogSeekError) as caught:
            lex("# a comment")
        self.assertEqual(caught.exception.category, "lexical")

    def test_lone_bang_is_a_lexical_error(self):
        with self.assertRaises(LogSeekError) as caught:
            lex('WHERE "severity" ! "ERROR"')
        self.assertEqual(caught.exception.category, "lexical")

    def test_lex_does_not_touch_the_filesystem(self):
        tokens = lex('LOAD "does-not-exist.csv"')
        self.assertEqual(tokens[1].value, "does-not-exist.csv")


if __name__ == "__main__":
    unittest.main()
