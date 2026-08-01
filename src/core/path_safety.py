"""Lexical path normalization and reparse-point safety helpers."""

from __future__ import annotations

import os
from pathlib import Path


def lexical_absolute_path(
    value: str | os.PathLike[str],
    *,
    working_directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Return an absolute normalized path without resolving filesystem aliases.

    Windows can expose the same directory through both a long path and an 8.3
    short-name alias (for example ``runneradmin`` and ``RUNNER~1``). Calling
    ``Path.resolve`` changes that spelling and makes otherwise pure path
    contracts depend on the host's alias configuration. Lexical normalization
    removes ``.`` and ``..`` without following symlinks, junctions, or aliases.
    """

    path = Path(value).expanduser()
    if not path.is_absolute():
        base = Path.cwd() if working_directory is None else Path(working_directory)
        if not base.is_absolute():
            base = Path.cwd() / base
        path = base / path
    return Path(os.path.normpath(os.fspath(path)))


def is_linklike(path: Path) -> bool:
    """Return whether one path component is a symlink or Windows junction."""

    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker and checker())
    except OSError:
        # Safety callers must fail closed when a component cannot be inspected.
        return True


def path_traverses_linklike(value: str | os.PathLike[str]) -> bool:
    """Inspect every lexical component without mistaking an 8.3 alias for a link."""

    path = lexical_absolute_path(value)
    return any(is_linklike(candidate) for candidate in (path, *path.parents))


def same_existing_path(
    left: str | os.PathLike[str],
    right: str | os.PathLike[str],
) -> bool:
    """Compare existing paths by filesystem identity, with a lexical fallback."""

    try:
        return os.path.samefile(left, right)
    except OSError:
        return lexical_absolute_path(left) == lexical_absolute_path(right)
