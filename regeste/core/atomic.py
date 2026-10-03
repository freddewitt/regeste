"""Atomic file writes: write to a sibling temp file, then `os.replace`.

An interrupted export never leaves a truncated file in place of a good one.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Yield a temp path next to `path`; on clean exit it replaces `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        yield tmp
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8", newline: str | None = None) -> None:
    with atomic_path(path) as tmp:
        with tmp.open("w", encoding=encoding, newline=newline) as f:
            f.write(text)


def atomic_output(func):
    """Decorator: run `func` with its `output_path` argument pointed at a temp file.

    The real file is replaced only if `func` returns normally. If `func` returns
    the (temp) path it was given, the real path is returned instead.
    """
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        real = Path(bound.arguments["output_path"])
        with atomic_path(real) as tmp:
            bound.arguments["output_path"] = tmp
            result = func(*bound.args, **bound.kwargs)
        return real if result == tmp else result

    return wrapper
