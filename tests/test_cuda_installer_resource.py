import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_cuda_installer import verify_files


class CudaInstallerResourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.helper = Path(self.temporary.name) / "cuda-installer"
        self.helper.mkdir()
        for relative, payload in {
            "python.exe": b"fixed-python",
            "LICENSE.txt": b"PSF license fixture",
            "component-requirements.txt": (
                Path(__file__).resolve().parents[1] / "requirements-cuda-component-lock.txt"
            ).read_bytes(),
            "Lib/site-packages/pip/__main__.py": b"# pinned pip fixture\n",
            "Lib/site-packages/pip/_vendor/distlib/t64.exe": b"fixed x64 console launcher",
            "Lib/site-packages/pip-26.1.2.dist-info/licenses/LICENSE.txt": b"MIT fixture",
        }.items():
            path = self.helper / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self._write_manifest()

    def _write_manifest(self):
        files = []
        for path in sorted(self.helper.rglob("*")):
            if not path.is_file() or path.name == "asset-manifest.json":
                continue
            files.append({
                "path": path.relative_to(self.helper).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            })
        (self.helper / "asset-manifest.json").write_text(json.dumps({
            "schemaVersion": 1,
            "assetId": "e7.cuda-installer",
            "layout": "cuda-installer/python.exe",
            "architecture": "x64",
            "python": {"version": "3.12.10", "abiTag": "cp312"},
            "installer": {
                "name": "pip",
                "version": "26.1.2",
                "consoleLauncherResource": "Lib/site-packages/pip/_vendor/distlib/t64.exe",
            },
            "component": {
                "displayPackage": "cupy-cuda13x[ctk]==14.1.1",
                "requirements": "component-requirements.txt",
                "requirementsSha256": "c39d7b64e59aa31e7125a6efebf4112f8591e42f114f72269f90dec7b0544ed4",
                "dependencyResolution": "disabled-with-pip-no-deps",
                "packages": [
                    {"name": line.split("==")[0], "version": line.split("==")[1]}
                    for line in (
                        Path(__file__).resolve().parents[1] / "requirements-cuda-component-lock.txt"
                    ).read_text(encoding="utf-8").splitlines()
                ],
            },
            "files": files,
        }), encoding="utf-8")

    def test_exact_inventory_is_accepted(self):
        manifest = verify_files(self.helper)
        self.assertEqual(manifest["python"]["abiTag"], "cp312")

    def test_tampered_file_is_rejected(self):
        (self.helper / "python.exe").write_bytes(b"tampered")
        with self.assertRaisesRegex(AssertionError, "integrity verification"):
            verify_files(self.helper)

    def test_missing_file_is_rejected(self):
        (self.helper / "LICENSE.txt").unlink()
        with self.assertRaisesRegex(AssertionError, "inventory drift"):
            verify_files(self.helper)

    def test_orphan_file_is_rejected(self):
        (self.helper / "orphan.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "inventory drift"):
            verify_files(self.helper)

    def test_second_executable_is_rejected_even_when_declared(self):
        (self.helper / "pythonw.exe").write_bytes(b"unexpected launcher")
        self._write_manifest()
        with self.assertRaisesRegex(AssertionError, "Unexpected CUDA installer executables"):
            verify_files(self.helper)


if __name__ == "__main__":
    unittest.main()
