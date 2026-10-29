"""Shared test helpers: repository paths, runtime CSVs, and stream capture.

The committed dataset at ``tests/sample_data/sample_logs.csv`` is read-only test
input (brief §10.1). Every other CSV a test needs is written into a temporary
directory at runtime (brief §10.2) from one of the text constants below.
"""

import contextlib
import io
import os
from typing import Iterator, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
QUERIES = os.path.join(TESTS_DIR, "queries")
SAMPLE_DATA = os.path.join(TESTS_DIR, "sample_data", "sample_logs.csv")

HEADER = "timestamp,service,event_code,severity,message"

# Verified facts about the committed dataset (brief §10.1, §14 checksum box).
SAMPLE_ROW_COUNT = 10000
SAMPLE_SHA256 = "24830dabd8a61e9caf62f2f93eabd93c32bbaecf7e6b8eaf509550d2d000a909"

# --- Runtime case text (brief §10.2) ---------------------------------------

HEADER_ONLY_TEXT = HEADER + "\n"

RAGGED_TEXT = HEADER + "\n" + "2026-10-01 09:15:00,payments,4021,ERROR\n"

BAD_HEADER_TEXT = (
    "timestamp,service,event_code,severity,Message\n"
    "2026-10-01 09:15:00,payments,4021,ERROR,renamed\n"
)

BOM_TEXT = (
    "\ufeff"
    + HEADER
    + "\n"
    + '2026-10-01 09:15:00,payments,4021,ERROR,"bom row"\n'
)

QUOTED_TEXT = (
    HEADER + "\n"
    + '2026-10-01 09:15:00,payments,4021,ERROR,"retry, limit exceeded"\n'
    + '2026-10-01 09:16:00,auth,1180,ERROR,"said ""connection failed"" twice"\n'
    + '2026-10-01 09:17:00,search,5501,INFO,"line one\nline two"\n'
)

WIDE_TEXT = (
    HEADER + "\n"
    + '2026-10-01 09:15:00,payments,4021,ERROR,'
    + '"this message is deliberately longer than forty characters"\n'
)

MALFORMED_TIMESTAMPS_TEXT = (
    HEADER + "\n"
    + '2026-10-01T09:15:00,payments,4021,ERROR,"uses a T separator"\n'
    + '2026-10-01 09:00,auth,1180,ERROR,"missing seconds"\n'
    + "2026-02-30 10:00:00,search,5501,INFO,\"impossible calendar date\"\n"
)

BLANK_LINE_TEXT = (
    HEADER + "\n" + "2026-10-01 09:00:00,a,1,INFO,x\n" + "\n"
)

UNDECODABLE_BYTES = HEADER.encode("utf-8") + b"\n\xff\xfe,a,1,INFO,x\n"


def write_csv(directory: str, name: str, text: str) -> str:
    """Write ``text`` to directory/name using binary-safe newline handling; return the path."""
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def write_bytes(directory: str, name: str, data: bytes) -> str:
    """Write raw ``data`` to directory/name and return the path."""
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def read_bytes(path: str) -> bytes:
    """Return the raw bytes of the file at ``path``."""
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path: str) -> str:
    """Return the text of ``path`` as UTF-8 with line endings left intact."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def sample_path() -> str:
    """Return the sample dataset path with forward slashes, for use inside a query."""
    return SAMPLE_DATA.replace(os.sep, "/")


@contextlib.contextmanager
def capture_streams() -> Iterator[Tuple[io.StringIO, io.StringIO]]:
    """Capture stdout and stderr for the duration of the block."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err
