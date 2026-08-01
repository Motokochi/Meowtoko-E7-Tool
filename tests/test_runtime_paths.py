import unittest

from src.desktop.runtime_paths import (
    resolve_adb_path,
    resolve_nvidia_smi_path,
    resolve_ollama_path,
    resolve_tesseract_path,
)


class RuntimePathTests(unittest.TestCase):
    def test_tesseract_prefers_future_bundled_resource(self):
        expected = r"C:\E7\resources\tesseract\tesseract.exe"

        result = resolve_tesseract_path(
            environment={"E7_RESOURCES_PATH": r"C:\E7\resources"},
            exists=lambda path: path == expected,
            which=lambda _name: r"C:\PATH\tesseract.exe",
        )

        self.assertEqual(result, expected)

    def test_ollama_uses_path_before_known_install_directories(self):
        result = resolve_ollama_path(
            environment={"LOCALAPPDATA": r"C:\Users\test\AppData\Local"},
            exists=lambda _path: True,
            which=lambda name: rf"C:\PATH\{name}.exe",
        )

        self.assertEqual(result, r"C:\PATH\ollama.exe")

    def test_adb_resolves_an_explicit_file_before_path(self):
        configured = r"C:\Android\platform-tools\adb.exe"

        result = resolve_adb_path(
            configured,
            exists=lambda path: path == configured,
            which=lambda _name: r"C:\PATH\adb.exe",
        )

        self.assertEqual(result, configured)

    def test_nvidia_smi_uses_driver_utility_without_nvcc(self):
        expected = r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"

        result = resolve_nvidia_smi_path(
            environment={"ProgramFiles": r"C:\Program Files"},
            exists=lambda path: path == expected,
            which=lambda name: self.fail(f"unexpected PATH lookup: {name}"),
        )

        self.assertEqual(result, expected)
        self.assertNotIn("nvcc", result.lower())


if __name__ == "__main__":
    unittest.main()
