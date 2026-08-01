from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.core.settings_service import settings_path
from src.core.workspace_paths import resolve_user_data_directory


class WorkspacePathTests(unittest.TestCase):
    def test_default_development_data_is_local_and_resolution_has_no_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            selected = resolve_user_data_directory({}, working_directory=root)

            self.assertEqual(root / ".local" / "user-data", selected)
            self.assertFalse((root / ".local").exists())

    def test_absolute_and_relative_environment_overrides_remain_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            absolute = root / "absolute-data"

            self.assertEqual(
                absolute,
                resolve_user_data_directory(
                    {"E7_USER_DATA_DIR": str(absolute)},
                    working_directory=root / "ignored",
                ),
            )
            self.assertEqual(
                root / "portable-data",
                resolve_user_data_directory(
                    {"E7_USER_DATA_DIR": "portable-data"},
                    working_directory=root,
                ),
            )

    def test_relative_working_directory_is_joined_once_without_io(self) -> None:
        selected = resolve_user_data_directory(
            {"E7_USER_DATA_DIR": "portable-data"},
            working_directory="relative-workspace",
        )

        self.assertEqual(
            Path.cwd() / "relative-workspace" / "portable-data",
            selected,
        )
        self.assertFalse((Path.cwd() / "relative-workspace").exists())

    def test_explicit_settings_file_still_wins_over_user_data(self) -> None:
        explicit = Path("C:/isolated/settings.json")

        self.assertEqual(
            explicit,
            settings_path(
                {
                    "E7_SETTINGS_PATH": str(explicit),
                    "E7_USER_DATA_DIR": "ignored-data",
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
