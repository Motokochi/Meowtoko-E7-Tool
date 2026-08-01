"""Side-effect-free local path contracts shared by development entry points."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from src.core.path_safety import lexical_absolute_path


USER_DATA_DIRECTORY_ENV = "E7_USER_DATA_DIR"
DEFAULT_DEVELOPMENT_USER_DATA = Path(".local") / "user-data"


def resolve_user_data_directory(
    environment: Mapping[str, str] | None = None,
    *,
    working_directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve app data without creating it.

    Packaged Electron launches always provide an absolute ``E7_USER_DATA_DIR``
    under the application's Windows profile. The fallback is exclusively the
    repository-local development location.
    """

    values = os.environ if environment is None else environment
    base = Path.cwd() if working_directory is None else Path(working_directory)
    override = values.get(USER_DATA_DIRECTORY_ENV)
    if override is not None and override.strip():
        selected = Path(override.strip()).expanduser()
        if not selected.is_absolute():
            selected = base / selected
    else:
        selected = base / DEFAULT_DEVELOPMENT_USER_DATA
    return lexical_absolute_path(selected)
