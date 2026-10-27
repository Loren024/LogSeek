"""LogSeek: a small domain-specific language for querying CSV application logs.

Single-module implementation and command-line entry point. Standard library only.
Language behaviour is defined by LogSeek_Documentation_v0.6.md; the build contract
(file layout, exit codes, message strings) is defined by IMPLEMENTATION_BRIEF.md.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

SCHEMA_HEADERS: Tuple[str, ...] = (
    "timestamp",
    "Service",
    "event_code",
    "severity",
    "message",
)

TIMESTAMP_COLUMN = "timestamp"
COLUMN_SEPARATOR = "  "
COLUMN_CAP = 40
TRUNCATE_KEEP = 37
TRUNCATION_MARK = "..."
REQUIRED_HEADER = ",".join(SCHEMA_HEADERS)

EMPTY_RESULT_MESSAGE = "No matching records found."
CANCEL_MESSAGE = "Export cancelled."
ERROR_PREFIX = "logseek: error: "

KEYWORDS = frozenset(
    {
        "LOAD",
        "FIND",
        "WHERE",
        "AFTER",
        "BEFORE",
        "ORDER",
        "BY",
        "LIMIT",
        "EXPORT",
        "AND",
        "OR",
        "CONTAINS",
        "ASC",
        "DESC",
    }
)

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DATETIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$")

_STAGE_BY_COMMAND = {
    "LOAD": 0,
    "WHERE": 1,
    "AFTER": 2,
    "BEFORE": 3,
    "FIND": 4,
    "ORDER": 5,
    "LIMIT": 6,
    "EXPORT": 7,
}
_STAGE_NAMES = ("LOAD", "WHERE", "AFTER", "BEFORE", "FIND", "ORDER BY", "LIMIT", "EXPORT")


class LogSeekError(Exception):
    """A user-facing LogSeek error carrying a category, a detail, and an optional line."""

    def __init__(self, category: str, message: str, line: Optional[int] = None) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.line = line


class ExportCancelled(LogSeekError):
    """Raised when the user declines to overwrite an existing export destination."""

    def __init__(self) -> None:
        super().__init__("export", CANCEL_MESSAGE)


@dataclass
class Token:
    """A single lexical token with its source position."""

    kind: str
    value: str
    line: int
    col: int


@dataclass
class Condition:
    """One `column operator value` comparison inside a WHERE expression."""

    column: str
    operator: str
    value: str
    line: int


@dataclass
class Expr:
    """A boolean expression node: AND, OR, or a single condition."""

    kind: str
    line: int
    left: Optional["Expr"] = None
    right: Optional["Expr"] = None
    condition: Optional[Condition] = None


@dataclass(frozen=True)
class Boundary:
    """A timestamp boundary written by an AFTER or BEFORE command."""

    column: str
    value: str
    line: int


@dataclass
class Query:
    """A parsed LogSeek query (spec §12.4)."""

    source_path: str = ""
    load_line: int = 0
    where_conditions: List[Expr] = field(default_factory=list)
    after_boundaries: List[Boundary] = field(default_factory=list)
    before_boundaries: List[Boundary] = field(default_factory=list)
    output_columns: Optional[List[str]] = None
    find_line: int = 0
    sort_column: Optional[str] = None
    sort_direction: str = "ASC"
    order_line: int = 0
    limit: Optional[int] = None
    limit_raw: Optional[str] = None
    limit_line: int = 0
    export_path: Optional[str] = None
    export_line: int = 0


@dataclass
class CsvTable:
    """A loaded CSV file: its header row and its data rows."""

    headers: List[str]
    rows: List[List[str]]


@dataclass
class Result:
    """The outcome of executing a query."""

    columns: List[str]
    rows: List[List[str]]
    excluded_count: int
    timestamp_filtered: bool


def _parse_timestamp(value: str) -> Optional[datetime]:
    """Return the datetime for an accepted timestamp string, or None when malformed."""
    if _DATE_RE.match(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    if _DATETIME_RE.match(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None
def lex(text: str) -> List[Token]:
    """Turn query text into tokens, raising a lexical error on invalid input."""
    if text.startswith("\ufeff"):
        text = text[1:]
    tokens: List[Token] = []
    index = 0
    line = 1
    col = 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == " " or char == "\t":
            index += 1
            col += 1
            continue
        if char == "\r":
            if index + 1 < length and text[index + 1] == "\n":
                tokens.append(Token("NEWLINE", "\r\n", line, col))
                index += 2
                line += 1
                col = 1
                continue
            raise LogSeekError("lexical", "unexpected carriage return", line)
        if char == "\n":
            tokens.append(Token("NEWLINE", "\n", line, col))
            index += 1
            line += 1
            col = 1
            continue
        if char == '"':
            token, index, col = _lex_string(text, index, line, col)
            tokens.append(token)
            continue
        if char == "!":
            if index + 1 < length and text[index + 1] == "=":
                tokens.append(Token("NOT_EQUALS", "!=", line, col))
                index += 2
                col += 2
                continue
            raise LogSeekError("lexical", "unexpected character '!'", line)
        if char in "=(),":
            tokens.append(Token(_PUNCTUATION_KINDS[char], char, line, col))
            index += 1
            col += 1
            continue
        if char.isascii() and char.isdigit():
            start = index
            start_col = col
            while index < length and text[index].isascii() and text[index].isdigit():
                index += 1
                col += 1
            tokens.append(Token("INTEGER", text[start:index], line, start_col))
            continue
        if char.isascii() and char.isalpha():
            start = index
            start_col = col
            while index < length and text[index].isascii() and text[index].isalpha():
                index += 1
                col += 1
            word = text[start:index]
            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token("KEYWORD", upper, line, start_col))
            else:
                tokens.append(Token("WORD", word, line, start_col))
            continue
        raise LogSeekError("lexical", f"unexpected character {char!r}", line)
    tokens.append(Token("EOF", "", line, col))
    return tokens


_PUNCTUATION_KINDS = {
    "=": "EQUALS",
    "(": "LPAREN",
    ")": "RPAREN",
    ",": "COMMA",
}


def _lex_string(
    text: str, start: int, line: int, col: int
) -> Tuple[Token, int, int]:
    """Scan one quoted string, returning its token, the next index, and the next column."""
    index = start + 1
    start_col = col
    col += 1
    length = len(text)
    chars: List[str] = []
    while index < length:
        char = text[index]
        if char == '"':
            return Token("STRING", "".join(chars), line, start_col), index + 1, col + 1
        if char == "\\":
            if index + 1 >= length:
                raise LogSeekError("lexical", "dangling escape at end of input", line)
            escaped = text[index + 1]
            if escaped == '"' or escaped == "\\":
                chars.append(escaped)
            elif escaped == "\r" or escaped == "\n":
                raise LogSeekError("lexical", "unterminated quoted string", line)
            else:
                chars.append("\\")
                chars.append(escaped)
            index += 2
            col += 2
            continue
        if char == "\r" or char == "\n":
            raise LogSeekError("lexical", "unterminated quoted string", line)
        chars.append(char)
        index += 1
        col += 1
    raise LogSeekError("lexical", "unterminated quoted string", line)
def parse(tokens: List[Token]) -> Query:
    """Build a Query from tokens, raising a syntax error on invalid command structure."""
    lines = _split_lines(tokens)
    if not lines:
        raise LogSeekError("syntax", "query is empty: LOAD is required")
    parser = _Parser()
    for line_tokens in lines:
        parser.parse_line(line_tokens)
    return parser.query


def _split_lines(tokens: Iterable[Token]) -> List[List[Token]]:
    """Group tokens into one list per non-blank source line."""
    lines: List[List[Token]] = []
    current: List[Token] = []
    for token in tokens:
        if token.kind in ("NEWLINE", "EOF"):
            if current:
                lines.append(current)
            current = []
        else:
            current.append(token)
    return lines


def _is_keyword(token: Token, word: str) -> bool:
    """Return True when the token is the given keyword, ignoring case."""
    return token.kind == "KEYWORD" and token.value == word


class _Parser:
    """Recursive-descent parser over token lines, enforcing the fixed command order."""

    def __init__(self) -> None:
        self.query = Query()
        self._stage = -1

    def parse_line(self, tokens: List[Token]) -> None:
        """Parse one command line and record it on the query."""
        first = tokens[0]
        if first.kind != "KEYWORD":
            if first.kind == "WORD":
                raise LogSeekError("syntax", f'unknown command "{first.value}"', first.line)
            raise LogSeekError("syntax", f'unexpected token "{first.value}"', first.line)
        command = first.value
        self._check_order(command, _STAGE_BY_COMMAND[command], first.line)
        handler = getattr(self, "_parse_" + command.lower())
        handler(tokens)
        self._stage = max(self._stage, _STAGE_BY_COMMAND[command])

    def _check_order(self, command: str, stage: int, line: int) -> None:
        """Reject commands that break the required order or repeat when not allowed."""
        if self._stage < 0:
            if command != "LOAD":
                raise LogSeekError("syntax", "LOAD must be the first command", line)
            return
        if command == "LOAD":
            raise LogSeekError("syntax", "LOAD may appear only once", line)
        if self._stage == _STAGE_BY_COMMAND["EXPORT"]:
            raise LogSeekError("syntax", "EXPORT must be the last command", line)
        if stage < self._stage:
            earlier = _STAGE_NAMES[self._stage]
            raise LogSeekError("syntax", f"{command} must appear before {earlier}", line)
        if stage == self._stage and command not in ("WHERE", "AFTER", "BEFORE"):
            raise LogSeekError("syntax", f"{command} may appear only once", line)
    def _parse_load(self, tokens: List[Token]) -> None:
        """Parse `LOAD "path"`."""
        self._require_length(tokens, 2, "LOAD requires a quoted file path")
        path_token = self._require_kind(tokens[1], "STRING", "LOAD requires a quoted file path")
        self.query.source_path = path_token.value
        self.query.load_line = tokens[0].line

    def _parse_find(self, tokens: List[Token]) -> None:
        """Parse `FIND` with an optional comma-separated column list."""
        columns: List[str] = []
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token.kind != "STRING":
                raise LogSeekError("syntax", "FIND requires quoted column names", token.line)
            columns.append(token.value)
            index += 1
            if index < len(tokens):
                if tokens[index].kind != "COMMA":
                    raise LogSeekError(
                        "syntax",
                        f'unexpected token "{tokens[index].value}"',
                        tokens[index].line,
                    )
                index += 1
                if index >= len(tokens):
                    raise LogSeekError(
                        "syntax", "FIND requires a column name after ','", tokens[-1].line
                    )
        self.query.find_line = tokens[0].line
        if columns:
            self.query.output_columns = columns

    def _parse_where(self, tokens: List[Token]) -> None:
        """Parse `WHERE <expression>` and store the expression tree."""
        expression, index = self._parse_expression(tokens, 1)
        if index != len(tokens):
            token = tokens[index]
            raise LogSeekError("syntax", f'unexpected token "{token.value}"', token.line)
        self.query.where_conditions.append(expression)

    def _parse_after(self, tokens: List[Token]) -> None:
        """Parse `AFTER "timestamp" "boundary"`."""
        self.query.after_boundaries.append(self._parse_boundary(tokens, "AFTER"))

    def _parse_before(self, tokens: List[Token]) -> None:
        """Parse `BEFORE "timestamp" "boundary"`."""
        self.query.before_boundaries.append(self._parse_boundary(tokens, "BEFORE"))

    def _parse_boundary(self, tokens: List[Token], command: str) -> Boundary:
        """Parse the shared `AFTER`/`BEFORE` argument pair."""
        message = f"{command} requires a quoted column name and a quoted boundary"
        self._require_length(tokens, 3, message)
        column = self._require_kind(tokens[1], "STRING", message)
        value = self._require_kind(tokens[2], "STRING", message)
        return Boundary(column.value, value.value, tokens[0].line)

    def _parse_order(self, tokens: List[Token]) -> None:
        """Parse `ORDER BY "column" [ASC|DESC]`."""
        if len(tokens) < 2 or not _is_keyword(tokens[1], "BY"):
            raise LogSeekError("syntax", "ORDER must be followed by BY", tokens[0].line)
        if len(tokens) < 3:
            raise LogSeekError(
                "syntax", "ORDER BY requires a quoted column name", tokens[-1].line
            )
        column = self._require_kind(
            tokens[2], "STRING", "ORDER BY requires a quoted column name"
        )
        direction = "ASC"
        if len(tokens) > 3:
            token = tokens[3]
            if _is_keyword(token, "ASC"):
                direction = "ASC"
            elif _is_keyword(token, "DESC"):
                direction = "DESC"
            else:
                raise LogSeekError(
                    "syntax", f'invalid sort direction "{token.value}"', token.line
                )
            if len(tokens) > 4:
                raise LogSeekError(
                    "syntax", f'unexpected token "{tokens[4].value}"', tokens[4].line
                )
        self.query.sort_column = column.value
        self.query.sort_direction = direction
        self.query.order_line = tokens[0].line

    def _parse_limit(self, tokens: List[Token]) -> None:
        """Parse `LIMIT <integer>`."""
        self._require_length(tokens, 2, "LIMIT requires one positive integer")
        token = self._require_kind(
            tokens[1], "INTEGER", "LIMIT requires one positive integer"
        )
        self.query.limit = int(token.value)
        self.query.limit_raw = token.value
        self.query.limit_line = tokens[0].line

    def _parse_export(self, tokens: List[Token]) -> None:
        """Parse `EXPORT "path"`."""
        self._require_length(tokens, 2, "EXPORT requires a quoted file path")
        path_token = self._require_kind(
            tokens[1], "STRING", "EXPORT requires a quoted file path"
        )
        self.query.export_path = path_token.value
        self.query.export_line = tokens[0].line

    def _parse_expression(self, tokens: List[Token], index: int) -> Tuple[Expr, int]:
        """Parse an expression starting at index, returning the node and next index."""
        return self._parse_or(tokens, index)

    def _parse_or(self, tokens: List[Token], index: int) -> Tuple[Expr, int]:
        """Parse one or more AND-expressions joined by OR."""
        left, index = self._parse_and(tokens, index)
        while index < len(tokens) and _is_keyword(tokens[index], "OR"):
            right, index = self._parse_and(tokens, index + 1)
            left = Expr("OR", left.line, left=left, right=right)
        return left, index

    def _parse_and(self, tokens: List[Token], index: int) -> Tuple[Expr, int]:
        """Parse one or more primary expressions joined by AND."""
        left, index = self._parse_primary(tokens, index)
        while index < len(tokens) and _is_keyword(tokens[index], "AND"):
            right, index = self._parse_primary(tokens, index + 1)
            left = Expr("AND", left.line, left=left, right=right)
        return left, index

    def _parse_primary(self, tokens: List[Token], index: int) -> Tuple[Expr, int]:
        """Parse either a parenthesised expression or a single condition."""
        if index >= len(tokens):
            raise LogSeekError("syntax", "expected a condition", tokens[-1].line)
        token = tokens[index]
        if token.kind == "LPAREN":
            if index + 1 < len(tokens) and tokens[index + 1].kind == "RPAREN":
                raise LogSeekError("syntax", "empty parentheses are not allowed", token.line)
            expression, index = self._parse_expression(tokens, index + 1)
            if index >= len(tokens) or tokens[index].kind != "RPAREN":
                raise LogSeekError("syntax", "missing closing parenthesis", token.line)
            return expression, index + 1
        if token.kind != "STRING":
            raise LogSeekError("syntax", "expected a quoted column name", token.line)
        return self._parse_condition(tokens, index)

    def _parse_condition(self, tokens: List[Token], index: int) -> Tuple[Expr, int]:
        """Parse `"column" operator "value"` starting at a column token."""
        column_token = tokens[index]
        index += 1
        if index >= len(tokens):
            raise LogSeekError(
                "syntax", "expected an operator after the column name", column_token.line
            )
        operator_token = tokens[index]
        if operator_token.kind == "EQUALS":
            operator = "="
        elif operator_token.kind == "NOT_EQUALS":
            operator = "!="
        elif _is_keyword(operator_token, "CONTAINS"):
            operator = "CONTAINS"
        else:
            raise LogSeekError(
                "syntax", f'unsupported operator "{operator_token.value}"', operator_token.line
            )
        index += 1
        if index >= len(tokens) or tokens[index].kind != "STRING":
            raise LogSeekError(
                "syntax", "expected a quoted value after the operator", operator_token.line
            )
        value_token = tokens[index]
        condition = Condition(column_token.value, operator, value_token.value, column_token.line)
        return Expr("COND", column_token.line, condition=condition), index + 1

    @staticmethod
    def _require_length(tokens: List[Token], expected: int, message: str) -> None:
        """Raise a syntax error unless the line has exactly the expected token count."""
        if len(tokens) < expected:
            raise LogSeekError("syntax", message, tokens[-1].line)
        if len(tokens) > expected:
            raise LogSeekError(
                "syntax", f'unexpected token "{tokens[expected].value}"', tokens[expected].line
            )

    @staticmethod
    def _require_kind(token: Token, kind: str, message: str) -> Token:
        """Raise a syntax error unless the token has the expected kind."""
        if token.kind != kind:
            raise LogSeekError("syntax", message, token.line)
        return token
def validate_semantics(q: Query) -> None:
    """Check column names, FIND duplicates, LIMIT bounds, and timestamp boundaries."""
    for expression in q.where_conditions:
        _validate_expression(expression)
    for boundary in q.after_boundaries:
        _validate_boundary(boundary, "AFTER")
    for boundary in q.before_boundaries:
        _validate_boundary(boundary, "BEFORE")
    _validate_find_columns(q)
    if q.sort_column is not None:
        _validate_column(q.sort_column, q.order_line)
    _validate_limit(q)


def _validate_expression(expression: Expr) -> None:
    """Check every column referenced by a WHERE expression."""
    if expression.kind == "COND":
        condition = expression.condition
        if condition is not None:
            _validate_column(condition.column, condition.line)
        return
    if expression.left is not None:
        _validate_expression(expression.left)
    if expression.right is not None:
        _validate_expression(expression.right)


def _validate_column(column: str, line: int) -> None:
    """Raise a semantic error when a column name is not in the fixed schema."""
    if column not in SCHEMA_HEADERS:
        raise LogSeekError("semantic", f'unknown column "{column}"', line)


def _validate_boundary(boundary: Boundary, command: str) -> None:
    """Check the column and the date format of an AFTER or BEFORE boundary."""
    if boundary.column != TIMESTAMP_COLUMN:
        if boundary.column in SCHEMA_HEADERS:
            raise LogSeekError(
                "semantic", f'{command} requires the "timestamp" column', boundary.line
            )
        raise LogSeekError("semantic", f'unknown column "{boundary.column}"', boundary.line)
    if _parse_timestamp(boundary.value) is None:
        raise LogSeekError(
            "timestamp", f'invalid boundary "{boundary.value}"', boundary.line
        )


def _validate_find_columns(q: Query) -> None:
    """Check that FIND columns exist and are not repeated."""
    if q.output_columns is None:
        return
    seen = set()
    for column in q.output_columns:
        _validate_column(column, q.find_line)
        if column in seen:
            raise LogSeekError(
                "semantic", f'duplicate column "{column}" in FIND', q.find_line
            )
        seen.add(column)


def _validate_limit(q: Query) -> None:
    """Require LIMIT to be a positive integer without a leading zero."""
    if q.limit_raw is None:
        return
    if q.limit is None or q.limit <= 0 or q.limit_raw.startswith("0"):
        raise LogSeekError("semantic", "LIMIT must be a positive integer", q.limit_line)


def load_csv(path: str) -> CsvTable:
    """Load a CSV file and validate its extension, encoding, header, and row shape."""
    if not path.endswith(".csv"):
        raise LogSeekError(
            "input", f'file "{path}" must have a lowercase .csv extension'
        )
    if os.path.isdir(path):
        # Windows reports directories as PermissionError, so check before opening.
        raise LogSeekError("input", f'cannot open "{path}": is a directory')
    rows = _read_csv_rows(path)
    if not rows:
        raise LogSeekError("input", f'"{path}" is empty: missing header row')
    if rows[0] != list(SCHEMA_HEADERS):
        raise LogSeekError("input", f"expected header '{REQUIRED_HEADER}'")
    data: List[List[str]] = []
    for offset, row in enumerate(rows[1:], start=2):
        if not row:
            raise LogSeekError("input", f"blank line in CSV data at row {offset}")
        if len(row) != len(SCHEMA_HEADERS):
            raise LogSeekError(
                "input",
                f"row {offset} has {len(SCHEMA_HEADERS)} expected fields, found {len(row)}",
            )
        data.append(row)
    return CsvTable(list(SCHEMA_HEADERS), data)


def _read_csv_rows(path: str) -> List[List[str]]:
    """Read every CSV record from disk, mapping I/O failures to input errors."""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return [row for row in csv.reader(handle)]
    except FileNotFoundError:
        raise LogSeekError("input", f'cannot open "{path}": file not found') from None
    except IsADirectoryError:
        raise LogSeekError("input", f'cannot open "{path}": is a directory') from None
    except PermissionError:
        raise LogSeekError("input", f'cannot open "{path}": permission denied') from None
    except UnicodeDecodeError:
        raise LogSeekError("input", f'cannot decode "{path}": not valid UTF-8') from None
    except csv.Error as exc:
        raise LogSeekError("input", f'cannot parse "{path}": {exc}') from None
    except OSError as exc:
        detail = exc.strerror if exc.strerror else str(exc)
        raise LogSeekError("input", f'cannot read "{path}": {detail}') from None
def execute(q: Query, table: CsvTable) -> Result:
    """Apply filters, sorting, limiting, and projection to a loaded table."""
    position = {name: index for index, name in enumerate(table.headers)}
    rows = list(table.rows)
    for expression in q.where_conditions:
        rows = [row for row in rows if _evaluate(expression, row, position)]
    excluded_count = 0
    timestamp_filtered = bool(q.after_boundaries or q.before_boundaries)
    if timestamp_filtered:
        after = [_parse_timestamp(boundary.value) for boundary in q.after_boundaries]
        before = [_parse_timestamp(boundary.value) for boundary in q.before_boundaries]
        rows, excluded_count = _filter_timestamps(
            rows, position[TIMESTAMP_COLUMN], after, before
        )
    if q.sort_column is not None:
        rows = _sort_rows(rows, position[q.sort_column], q.sort_column, q.sort_direction)
    if q.limit is not None:
        rows = rows[: q.limit]
    columns = list(SCHEMA_HEADERS) if q.output_columns is None else list(q.output_columns)
    indices = [position[column] for column in columns]
    projected = [[row[index] for index in indices] for row in rows]
    return Result(columns, projected, excluded_count, timestamp_filtered)


def _evaluate(expression: Expr, row: List[str], position: dict) -> bool:
    """Evaluate one WHERE expression against a single row."""
    if expression.kind == "AND":
        return _evaluate(expression.left, row, position) and _evaluate(
            expression.right, row, position
        )
    if expression.kind == "OR":
        return _evaluate(expression.left, row, position) or _evaluate(
            expression.right, row, position
        )
    condition = expression.condition
    value = row[position[condition.column]]
    if condition.operator == "=":
        return value == condition.value
    if condition.operator == "!=":
        return value != condition.value
    return condition.value in value


def _filter_timestamps(
    rows: List[List[str]],
    position: int,
    after: List[Optional[datetime]],
    before: List[Optional[datetime]],
) -> Tuple[List[List[str]], int]:
    """Drop rows failing an AFTER/BEFORE boundary and count malformed-timestamp rows."""
    kept: List[List[str]] = []
    excluded = 0
    for row in rows:
        stamp = _parse_timestamp(row[position])
        if stamp is None:
            excluded += 1
            continue
        if any(cutoff is not None and not stamp > cutoff for cutoff in after):
            continue
        if any(cutoff is not None and not stamp < cutoff for cutoff in before):
            continue
        kept.append(row)
    return kept, excluded


def _sort_rows(
    rows: List[List[str]], position: int, column: str, direction: str
) -> List[List[str]]:
    """Return rows sorted by one column, stable, with malformed timestamps last."""
    descending = direction == "DESC"
    if column != TIMESTAMP_COLUMN:
        return sorted(rows, key=lambda row: row[position], reverse=descending)
    sortable: List[List[str]] = []
    malformed: List[List[str]] = []
    for row in rows:
        if _parse_timestamp(row[position]) is None:
            malformed.append(row)
        else:
            sortable.append(row)
    sortable.sort(key=lambda row: _parse_timestamp(row[position]), reverse=descending)
    return sortable + malformed
def render(result: Result) -> str:
    """Render a result as the fixed plain-text table, ending with exactly one newline."""
    if not result.rows:
        return EMPTY_RESULT_MESSAGE + "\n"
    display_rows = [[_display_cell(value) for value in row] for row in result.rows]
    widths = []
    for index, header in enumerate(result.columns):
        widest = len(header)
        for row in display_rows:
            if len(row[index]) > widest:
                widest = len(row[index])
        widths.append(widest)
    lines = [_format_row(result.columns, widths)]
    lines.append(_format_row(["-" * width for width in widths], widths))
    for row in display_rows:
        lines.append(_format_row(row, widths))
    return "\n".join(lines) + "\n"


def _display_cell(value: str) -> str:
    """Escape line breaks and tabs for display, then truncate to the column cap."""
    text = (
        value.replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
        .replace("\t", "\\t")
    )
    if len(text) > COLUMN_CAP:
        text = text[:TRUNCATE_KEEP] + TRUNCATION_MARK
    return text


def _format_row(cells: List[str], widths: List[int]) -> str:
    """Join padded cells with the column separator and right-trim the result."""
    padded = [
        cell.ljust(width) for cell, width in zip(cells, widths)
    ]
    return COLUMN_SEPARATOR.join(padded).rstrip()
def export_csv(path: str, result: Result, source_path: str) -> None:
    """Write a result to a new CSV file after destination and overwrite checks."""
    if not path.endswith(".csv"):
        raise LogSeekError(
            "export", f'destination "{path}" must have a lowercase .csv extension'
        )
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        raise LogSeekError(
            "export", f'destination directory "{directory}" does not exist'
        )
    if _same_file(path, source_path):
        raise LogSeekError("export", "refusing to overwrite the source CSV")
    if os.path.exists(path) and not _confirm_overwrite(path):
        raise ExportCancelled()
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(result.columns)
            writer.writerows(result.rows)
    except OSError as exc:
        detail = exc.strerror if exc.strerror else str(exc)
        raise LogSeekError("export", f'cannot write "{path}": {detail}') from None


def _same_file(first: str, second: str) -> bool:
    """Return True when two paths resolve to the same file, including via hard links."""
    try:
        if os.path.exists(first) and os.path.exists(second) and os.path.samefile(first, second):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(
        os.path.realpath(second)
    )


def _confirm_overwrite(path: str) -> bool:
    """Ask whether to overwrite path, returning True only for an answer of y or Y."""
    prompt = f"{path} already exists. Overwrite? [y/N] "
    answer = _read_overwrite_answer(prompt)
    return answer.strip() in ("y", "Y")


def _read_overwrite_answer(prompt: str) -> str:
    """Show the prompt when input is interactive and return the typed line, else ''."""
    handle, owned = _open_interactive_input()
    if handle is None:
        return ""
    try:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        return handle.readline()
    except (OSError, ValueError):
        return ""
    finally:
        if owned:
            handle.close()


def _open_interactive_input() -> Tuple[Optional[object], bool]:
    """Open a terminal for the overwrite prompt, returning the stream and ownership."""
    stdin = sys.stdin
    interactive = False
    if stdin is not None:
        try:
            interactive = stdin.isatty()
        except (OSError, ValueError):
            interactive = False
    if not interactive:
        # Without an interactive stdin the prompt cannot be answered, so decline silently.
        return None, False
    try:
        return open("/dev/tty", "r"), True
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            return open("CONIN$", "r"), True
        except OSError:
            pass
    return stdin, False
def main(argv: List[str]) -> int:
    """Run one LogSeek query and return the process exit code."""
    _configure_stdout()
    try:
        query = parse(lex(_read_query_text(argv)))
        validate_semantics(query)
        table = load_csv(query.source_path)
        result = execute(query, table)
        _write_result(result)
        if query.export_path is not None:
            export_csv(query.export_path, result, query.source_path)
            _report_exported(query.export_path, len(result.rows))
    except ExportCancelled:
        sys.stderr.write(CANCEL_MESSAGE + "\n")
        return 1
    except LogSeekError as exc:
        return _report_error(exc)
    return 0


def _read_query_text(argv: List[str]) -> str:
    """Read the query file named on the command line, or standard input when absent."""
    if len(argv) > 1:
        raise LogSeekError("invocation", "expected exactly one query file argument")
    if not argv:
        stdin = sys.stdin
        interactive = True
        if stdin is not None:
            try:
                interactive = stdin.isatty()
            except (OSError, ValueError):
                interactive = True
        if interactive:
            raise LogSeekError("invocation", "expected exactly one query file argument")
        return stdin.read()
    path = argv[0]
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        raise LogSeekError("io", f'cannot read query file "{path}": file not found') from None
    except IsADirectoryError:
        raise LogSeekError("io", f'cannot read query file "{path}": is a directory') from None
    except PermissionError:
        raise LogSeekError("io", f'cannot read query file "{path}": permission denied') from None
    except UnicodeDecodeError:
        raise LogSeekError(
            "io", f'cannot read query file "{path}": not valid UTF-8'
        ) from None
    except OSError as exc:
        detail = exc.strerror if exc.strerror else str(exc)
        raise LogSeekError("io", f'cannot read query file "{path}": {detail}') from None


def _write_result(result: Result) -> None:
    """Write the rendered table or empty message, then the exclusion count."""
    sys.stdout.write(render(result))
    if result.timestamp_filtered:
        sys.stdout.write("\n" + _exclusion_message(result.excluded_count) + "\n")
    sys.stdout.flush()


def _exclusion_message(count: int) -> str:
    """Return the canonical malformed-timestamp exclusion line."""
    noun = "row" if count == 1 else "rows"
    return f"{count} {noun} excluded: malformed timestamp."


def _report_exported(path: str, count: int) -> None:
    """Write the optional export confirmation to stderr."""
    noun = "row" if count == 1 else "rows"
    sys.stderr.write(f'Exported {count} {noun} to "{path}".\n')


def _report_error(exc: LogSeekError) -> int:
    """Write one error to stderr and return the matching exit code."""
    location = f" (line {exc.line})" if exc.line is not None else ""
    sys.stderr.write(f"{ERROR_PREFIX}{exc.category}: {exc.message}{location}\n")
    return 2 if exc.category == "invocation" else 1


def _configure_stdout() -> None:
    """Force UTF-8 on stdout so non-ASCII messages cannot raise on Windows consoles."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
