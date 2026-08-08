"""Atomic JSON writing.

Both picks.json and history.json are committed by the GitHub workflow, which
copies whatever is on disk. A plain `open(path, "w")` truncates the file before
writing a byte, so a crash mid-write leaves a truncated file that the workflow
would then commit -- publishing corruption over a previously-good file.

Writing to a temp file in the SAME directory and then os.replace()ing it makes
the swap atomic: readers see either the old complete file or the new complete
file, never a partial one. Same-directory matters because os.replace is only
atomic within a filesystem.
"""
import json
import os
import tempfile
from typing import Any


def write_json(path: str, obj: Any) -> None:
    """Serialise `obj` to `path` atomically, preserving the old file on failure.

    Raises whatever json.dump raises (e.g. TypeError for unserialisable input),
    having left the existing file untouched and cleaned up the temp file.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
