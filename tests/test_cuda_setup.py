import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import src.desktop.cuda_setup as cuda_setup

from src.desktop.cuda_setup import (
    CUDA_COMPONENT_PACKAGE,
    CUDA_COMPONENT_SOURCE,
    CudaComponentCancelled,
    CudaComponentManager,
    CudaComponentUnavailable,
    activate_cuda_component,
    resolve_cuda_installer_python,
    resolve_cuda_component_requirements,
    valid_cuda_component_requirements,
    valid_component_directory,
)


class CudaComponentSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.user_data = Path(self.temporary.name) / "user-data"
        self.commands = []

    def runner(self, command, timeout, cancelled):
        self.commands.append((tuple(command), timeout))
        if cancelled():
            raise CudaComponentCancelled("cancelled")
        if "pip" in command:
            target = Path(command[command.index("--target") + 1])
            self.populate_component(target)

    @staticmethod
    def populate_component(target: Path, *, marker: str | None = None) -> None:
        for relative in cuda_setup.CUDA_COMPONENT_REQUIRED_PATHS:
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic component fixture\n", encoding="utf-8")
        if marker is not None:
            (target / "component.marker").write_text(marker, encoding="utf-8")

    def manager(self):
        return CudaComponentManager(
            self.user_data,
            installer_resolver=lambda: r"C:\trusted\cuda-installer\python.exe",
            requirements_resolver=lambda _installer: r"C:\trusted\cuda-installer\component-requirements.txt",
            requirements_validator=lambda _requirements: True,
            runner=self.runner,
        )

    def test_status_is_read_only_and_cpu_safe(self):
        manager = self.manager()

        status = manager.status()

        self.assertFalse(status.installed)
        self.assertTrue(status.installer_available)
        self.assertFalse(self.user_data.exists())
        self.assertNotIn("cupy", sys.modules)

    def test_install_uses_only_fixed_binary_wheels_and_activates_atomic_component(self):
        manager = self.manager()
        progress = []

        with ExitStack() as activation:
            if cuda_setup.os.name == "nt":
                activation.enter_context(
                    patch.object(
                        cuda_setup.os,
                        "add_dll_directory",
                        return_value=unittest.mock.Mock(),
                    )
                )
                activation.enter_context(
                    patch.object(cuda_setup.ctypes, "WinDLL", return_value=object())
                )
            manager.install_or_repair(
                lambda value, message: progress.append((value, message)),
                lambda: False,
            )

            self.assertTrue(valid_component_directory(manager.directory))
            self.assertTrue(activate_cuda_component(self.user_data))
        self.addCleanup(lambda: sys.path.remove(str(manager.directory)) if str(manager.directory) in sys.path else None)
        self.addCleanup(cuda_setup._CUDA_DLL_RESOURCES.clear)
        install = self.commands[0][0]
        self.assertIn(CUDA_COMPONENT_SOURCE, install)
        self.assertIn("--only-binary=:all:", install)
        self.assertIn("--no-deps", install)
        self.assertIn("--requirement", install)
        self.assertTrue(install[-1].endswith("component-requirements.txt"))
        self.assertIn("--isolated", install)
        self.assertEqual(install[1:3], ("-I", "-B"))
        self.assertNotIn("nvcc", " ".join(install).lower())
        self.assertEqual(progress[-1][0], 1.0)
        self.assertFalse(any(path.name.endswith((".staging", ".backup")) for path in manager.directory.parent.iterdir()))

    @unittest.skipUnless(cuda_setup.os.name == "nt", "Windows DLL activation contract")
    def test_activation_preloads_target_installed_nvrtc_for_pathfinder(self):
        manager = self.manager()
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        dll_directory = manager.directory / cuda_setup.CUDA_COMPONENT_CUDA_DLL_DIRECTORY
        dll_directory.mkdir(parents=True, exist_ok=True)
        nvrtc = dll_directory / cuda_setup.CUDA_COMPONENT_NVRTC_DLL
        nvrtc.write_bytes(b"synthetic nvrtc fixture")
        directory_handle = unittest.mock.Mock()
        nvrtc_handle = object()
        cuda_setup._CUDA_DLL_RESOURCES.clear()

        with (
            patch.dict(cuda_setup.os.environ, {}, clear=False),
            patch.object(cuda_setup.os, "add_dll_directory", return_value=directory_handle) as add_directory,
            patch.object(cuda_setup.ctypes, "WinDLL", return_value=nvrtc_handle) as load_nvrtc,
        ):
            self.assertTrue(activate_cuda_component(self.user_data))
            self.assertTrue(activate_cuda_component(self.user_data))
            cuda_root = manager.directory / "nvidia" / "cu13"
            self.assertEqual(cuda_setup.os.environ["CUDA_PATH"], str(cuda_root))
            self.assertEqual(cuda_setup.os.environ["CUDA_HOME"], str(cuda_root))
            self.assertEqual(
                cuda_setup.os.environ["CUPY_CACHE_DIR"],
                str(self.user_data.resolve() / "cache" / "cupy"),
            )
            self.assertEqual(
                cuda_setup.os.environ["PATH"].split(cuda_setup.os.pathsep).count(str(dll_directory)),
                1,
            )

        add_directory.assert_called_once_with(str(dll_directory))
        load_nvrtc.assert_called_once_with(str(nvrtc))
        self.assertEqual(
            cuda_setup._CUDA_DLL_RESOURCES[str(dll_directory).casefold()],
            (directory_handle, nvrtc_handle),
        )
        self.addCleanup(cuda_setup._CUDA_DLL_RESOURCES.clear)
        self.addCleanup(lambda: sys.path.remove(str(manager.directory)) if str(manager.directory) in sys.path else None)

    def test_cancelled_repair_keeps_previous_valid_component(self):
        manager = self.manager()
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        original_manifest = (manager.directory / "component-manifest.json").read_bytes()

        def cancelled_runner(command, timeout, cancelled):
            raise CudaComponentCancelled("cancelled")

        manager.runner = cancelled_runner
        with self.assertRaises(CudaComponentCancelled):
            manager.install_or_repair(lambda _value, _message: None, lambda: True)

        self.assertTrue(valid_component_directory(manager.directory))
        self.assertEqual((manager.directory / "component-manifest.json").read_bytes(), original_manifest)

    def test_interrupted_atomic_publication_restores_previous_component(self):
        manager = self.manager()
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        marker = manager.directory / "previous-component.marker"
        marker.write_text("preserve", encoding="utf-8")

        original_replace = Path.replace

        def fail_publish(path, target):
            if path.name.endswith(".staging") and Path(target) == manager.directory:
                raise OSError("simulated publication interruption")
            return original_replace(path, target)

        with (
            patch.object(Path, "replace", fail_publish),
            self.assertRaisesRegex(OSError, "publication interruption"),
        ):
            manager.install_or_repair(lambda _value, _message: None, lambda: False)

        self.assertTrue(valid_component_directory(manager.directory))
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_post_publish_failure_never_rolls_back_the_committed_component(self):
        install_number = 0

        def versioned_runner(command, _timeout, _cancelled):
            nonlocal install_number
            if "pip" in command:
                install_number += 1
                target = Path(command[command.index("--target") + 1])
                self.populate_component(target, marker=f"component-{install_number}")

        manager = self.manager()
        manager.runner = versioned_runner
        manager.install_or_repair(lambda _value, _message: None, lambda: False)

        def fail_after_publish(value, _message):
            if value == 1.0:
                raise RuntimeError("simulated post-publish notification failure")

        with self.assertRaisesRegex(RuntimeError, "notification failure"):
            manager.install_or_repair(fail_after_publish, lambda: False)

        self.assertTrue(valid_component_directory(manager.directory))
        self.assertEqual(
            (manager.directory / "component.marker").read_text(encoding="utf-8"),
            "component-2",
        )

    def test_locked_backup_cleanup_is_nonfatal_after_atomic_publication(self):
        install_number = 0

        def versioned_runner(command, _timeout, _cancelled):
            nonlocal install_number
            if "pip" in command:
                install_number += 1
                target = Path(command[command.index("--target") + 1])
                self.populate_component(target, marker=f"component-{install_number}")

        manager = self.manager()
        manager.runner = versioned_runner
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        original_rmtree = cuda_setup.shutil.rmtree

        def locked_backup(path, *args, **kwargs):
            candidate = Path(path)
            if candidate.name.endswith(".backup"):
                marker = candidate / "component.marker"
                marker.unlink(missing_ok=True)
                raise PermissionError("simulated loaded native extension")
            return original_rmtree(path, *args, **kwargs)

        with patch.object(cuda_setup.shutil, "rmtree", side_effect=locked_backup):
            manager.install_or_repair(lambda _value, _message: None, lambda: False)

        self.assertTrue(valid_component_directory(manager.directory))
        self.assertEqual(
            (manager.directory / "component.marker").read_text(encoding="utf-8"),
            "component-2",
        )

    def test_manifest_and_cupy_directory_do_not_hide_missing_pathfinder_files(self):
        manager = self.manager()
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        (manager.directory / "cuda" / "pathfinder" / "__init__.py").unlink()

        self.assertFalse(valid_component_directory(manager.directory))
        self.assertFalse(manager.status().installed)

    def test_startup_recovery_restores_a_valid_interrupted_backup(self):
        manager = self.manager()
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        backup = manager.directory.parent / (
            f".{cuda_setup.CUDA_COMPONENT_DIRECTORY}-{'a' * 32}.backup"
        )
        manager.directory.replace(backup)

        self.assertTrue(cuda_setup.recover_cuda_component_transactions(self.user_data))
        self.assertTrue(valid_component_directory(manager.directory))
        self.assertFalse(backup.exists())

    def test_frozen_backend_includes_dynamic_cuda_pathfinder_stdlib_dependencies(self):
        spec = (Path(__file__).resolve().parents[1] / "packaging" / "e7-core.spec").read_text(encoding="utf-8")

        self.assertIn('"graphlib"', spec)
        self.assertIn('"ctypes.wintypes"', spec)

    def test_packaged_resolution_never_falls_back_to_system_python(self):
        existing = {
            str(Path(r"C:\E7\resources") / "cuda-installer" / "python.exe"),
            r"C:\untrusted\python.exe",
        }
        resolver = lambda path: path in existing

        packaged = resolve_cuda_installer_python(
            {
                "E7_RESOURCES_PATH": r"C:\E7\resources",
                "E7_CUDA_INSTALLER_PYTHON": r"C:\untrusted\python.exe",
            },
            frozen=True,
            executable=r"C:\system\python.exe",
            exists=resolver,
        )
        missing_packaged = resolve_cuda_installer_python(
            {"E7_CUDA_INSTALLER_PYTHON": r"C:\untrusted\python.exe"},
            frozen=True,
            executable=r"C:\system\python.exe",
            exists=resolver,
        )

        self.assertEqual(packaged, str(Path(r"C:\E7\resources") / "cuda-installer" / "python.exe"))
        self.assertIsNone(missing_packaged)

    def test_packaged_component_requirements_never_fall_back_to_repository(self):
        installer = str(Path(r"C:\E7\resources") / "cuda-installer" / "python.exe")
        adjacent = str(Path(installer).parent / "component-requirements.txt")

        self.assertEqual(
            resolve_cuda_component_requirements(installer, frozen=True, exists=lambda path: path == adjacent),
            adjacent,
        )
        self.assertIsNone(
            resolve_cuda_component_requirements(installer, frozen=True, exists=lambda _path: False),
        )

    def test_component_requirements_are_hash_pinned(self):
        repository_lock = Path(__file__).resolve().parents[1] / "requirements-cuda-component-lock.txt"
        self.assertTrue(valid_cuda_component_requirements(str(repository_lock)))
        tampered = Path(self.temporary.name) / "tampered-requirements.txt"
        tampered.write_text(repository_lock.read_text(encoding="utf-8") + "unexpected==1\n", encoding="utf-8")
        self.assertFalse(valid_cuda_component_requirements(str(tampered)))

    def test_invalid_component_graph_disables_packaged_setup(self):
        manager = CudaComponentManager(
            self.user_data,
            installer_resolver=lambda: r"C:\trusted\cuda-installer\python.exe",
            requirements_resolver=lambda _installer: r"C:\trusted\cuda-installer\component-requirements.txt",
            requirements_validator=lambda _requirements: False,
            runner=self.runner,
        )

        self.assertFalse(manager.status().installer_available)
        with self.assertRaisesRegex(CudaComponentUnavailable, "trusted GPU component installer"):
            manager.install_or_repair(lambda _value, _message: None, lambda: False)


if __name__ == "__main__":
    unittest.main()
