# LogSeek

LogSeek is a small domain-specific language (DSL) for querying structured application
logs stored in CSV files. You write a query in a plain-text file, run it once, and
LogSeek prints a table of matching rows, optionally exporting the result to another CSV.

## Requirements

Python **3.9 or newer**. LogSeek uses only the standard library, so there
is no build step, no installer, and no third-party dependency. A run needs three things:
the LogSeek program file, the query file, and the CSV being queried.

## Invocation

```
python logseek.py <query-file>
```

The query may also be piped in on standard input:

```
python logseek.py < errors_this_month.lsq
```

Relative paths inside the query are resolved against the **working directory** you run
LogSeek from, not against the location of the query file.

## A complete session

```
$ cd ~/logs
$ cat errors_this_month.lsq
LOAD "application_logs.csv"
WHERE "severity" = "ERROR"
AFTER "timestamp" "2026-10-01"
ORDER BY "timestamp" DESC
LIMIT 5

$ python logseek.py errors_this_month.lsq

timestamp            Service   event_code  severity  message
-------------------- --------- ----------- --------- -------------------------
2026-10-01 09:15:00  payments  4021        ERROR     retry limit exceeded
2026-10-01 08:44:02  auth      1180        ERROR     token expired

2 rows excluded: malformed timestamp.
```

If the query ends with `EXPORT` and the destination already exists, LogSeek asks first:

```
error_results.csv already exists. Overwrite? [y/N]:
```

Only `y` or `Y` overwrites. Anything else, or no input at all, declines the export and
leaves the existing file untouched. If the prompt cannot be shown because input is not
interactive, the export is refused rather than forced.

## Input data

The CSV must have exactly these five columns, with these exact header names in this
exact order:

```
timestamp,Service,event_code,severity,message
```

Missing, additional, renamed, or reordered columns are invalid. Column references in
queries must match the schema exactly, including capitalization: use `Service`, not
`service`. The file name must end with the lowercase `.csv` extension. A leading UTF-8
BOM is stripped before the header is validated. Input and output follow RFC 4180: fields
containing a comma, a double quote, or a line break are enclosed in double quotes, and a
literal double quote inside a quoted field is written as two double quotes.

## The query language

One command per line, in this fixed order. Only `LOAD` is required.

| Command | Purpose |
|---|---|
| `LOAD "path.csv"` | Required, first. Opens the CSV to query. |
| `WHERE <expression>` | Filters rows. May appear more than once. |
| `AFTER "timestamp" "YYYY-MM-DD[ HH:MM:SS]"` | Keeps rows strictly after the boundary. May repeat. |
| `BEFORE "timestamp" "YYYY-MM-DD[ HH:MM:SS]"` | Keeps rows strictly before the boundary. May repeat. |
| `FIND "col", "col"` | Chooses output columns. Omit it, or write bare `FIND`, for all columns. |
| `ORDER BY "col" [ASC\|DESC]` | Sorts by one column. Default is `ASC`. |
| `LIMIT <n>` | Keeps the first `n` rows of the current result. |
| `EXPORT "path.csv"` | Optional, last. Writes the result to a new CSV. |

Keywords are case-insensitive; column names are not. `WHERE` expressions support `=`,
`!=`, `CONTAINS`, `AND`, `OR`, and parentheses, for example:

```
WHERE ("severity" = "ERROR" OR "severity" = "WARNING") AND "message" CONTAINS "retry"
```

`AND` binds tighter than `OR`. Text comparison is exact and case-sensitive.

### Timestamps

`AFTER` and `BEFORE` accept `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`. Both are strict, and a
date-only boundary means midnight at the start of that date. When either command runs,
rows whose `timestamp` value cannot be parsed are excluded and counted; each excluded row
is counted once per query, however many timestamp filters are present.

### Out of scope

Joins, aggregation, regular expressions, live log monitoring, numeric comparison,
multi-column sorting, a REPL, and configurable formatting are not part of LogSeek.

## Terminal output

The table is fixed and non-configurable: a header row, a `-` separator row, then one row
per match. Columns are separated by two spaces and padded to the widest displayed value,
capped at 40 characters. Longer values are truncated to 37 characters followed by `...`.
Line breaks and tabs inside values are shown as `\n` and `\t` for display only — exported
data always keeps the original characters. Table lines are right-trimmed, so no trailing
padding is emitted.

When nothing matches, LogSeek prints:

```
No matching records found.
```

When a timestamp filter ran, the exclusion count is printed after the table, separated by
one blank line:

```
2 rows excluded: malformed timestamp.
```

The count is printed even when it is zero.

## Exit codes and streams

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Any user-facing error: lexical, syntax, semantic, input, timestamp, export, or I/O. |
| `2` | Invocation error — no query file and no piped query, or more than one argument. |

**Standard output carries data only**: the table and the exclusion count. Everything else —
errors, the overwrite prompt, and the optional `Exported N rows to "path".` confirmation —
goes to standard error.

Errors are written in one canonical shape:

```
logseek: error: semantic: unknown column "Severity" (line 3)
```

The `(line N)` suffix appears only when the error has a source line.

## Sample data

`tests/sample_data/sample_logs.csv` holds 10,000 committed rows and is the primary test
input. Treat it as read-only: LogSeek refuses to export over the CSV it loaded, and the
suite asserts the file's SHA-256 so any modification fails the build. Roughly one row in a
hundred carries a malformed timestamp (78 rows in total), so the exclusion count is easy to
observe. No generator script ships with the repository; the dataset is committed as-is.

## Running the tests

From the repository root:

```
python -m unittest discover tests
```

The suite covers the lexer, parser, semantic validation, CSV loader, executor, renderer,
exporter, and CLI, plus the seven single-command and seven combination units required by
the specification. `tests/queries/` holds one `.lsq` file per required unit; every CSV
those units need is written to a temporary directory at runtime by `tests/support.py`, so
there is no fixtures directory. The tests are self-contained: they create their own
temporary files and clean up after themselves.

The same suite runs under `pytest`:

```
python -m pytest tests
```

## Repository layout

```
logseek.py                  single-module implementation + CLI entry point
README.md                   this file
OPEN_QUESTIONS.md           ambiguities found during implementation
documentation/              language specification and implementation brief
tests/                      test modules and the 14 required query units
tests/support.py            shared helpers: paths, runtime CSVs, stream capture
tests/sample_data/          committed 10,000-row dataset (read-only test input)
```

