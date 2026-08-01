"""Deterministic, network-free smoke for the CPU and optional CUDA setup contracts."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.desktop.cuda_setup import (  # noqa: E402
    CUDA_COMPONENT_PACKAGE,
    CUDA_COMPONENT_REQUIRED_PATHS,
    CudaComponentManager,
    valid_component_directory,
)


def main() -> int:
    core = (ROOT / "requirements-core.txt").read_text(encoding="utf-8")
    build = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    cuda = (ROOT / "requirements-cuda.txt").read_text(encoding="utf-8")
    component = (ROOT / "requirements-cuda-component.txt").read_text(encoding="utf-8")
    component_lock = (ROOT / "requirements-cuda-component-lock.txt").read_text(encoding="utf-8")
    assert "cupy" not in core.lower()
    assert "nvidia" not in core.lower()
    assert "pyinstaller" not in core.lower()
    assert build.startswith("-r requirements-core.txt\n")
    assert cuda == "-r requirements-core.txt\n-r requirements-cuda-component.txt\n"
    assert component == f"{CUDA_COMPONENT_PACKAGE}\n"
    assert component_lock.startswith("cupy-cuda13x==14.1.1\n")
    assert len(component_lock.splitlines()) == 11

    commands: list[tuple[str, ...]] = []

    def fake_runner(command, _timeout, cancelled):
        assert not cancelled()
        command = tuple(command)
        commands.append(command)
        if "pip" in command:
            target = Path(command[command.index("--target") + 1])
            for relative in CUDA_COMPONENT_REQUIRED_PATHS:
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic component fixture\n", encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="e7-setup-smoke-") as temporary:
        user_data = Path(temporary) / "user-data"
        manager = CudaComponentManager(
            user_data,
            installer_resolver=lambda: r"C:\isolated\cuda-installer\python.exe",
            requirements_resolver=lambda _installer: r"C:\isolated\cuda-installer\component-requirements.txt",
            requirements_validator=lambda _requirements: True,
            runner=fake_runner,
        )
        assert not manager.status().installed
        assert not user_data.exists()
        manager.install_or_repair(lambda _value, _message: None, lambda: False)
        assert manager.status().installed
        assert valid_component_directory(manager.directory)
        if str(manager.directory) in sys.path:
            sys.path.remove(str(manager.directory))

    serialized_commands = " ".join(" ".join(command) for command in commands).lower()
    assert "nvcc" not in serialized_commands
    assert "--only-binary=:all:" in serialized_commands
    assert "--no-deps" in serialized_commands
    assert "--requirement" in serialized_commands
    assert all(command[1:3] == ("-I", "-B") for command in commands)
    assert CUDA_COMPONENT_PACKAGE.lower() not in serialized_commands
    print("E7_SETUP_SMOKE_OK core=cpu-safe gpu=pinned-sidecar source=pypi nvcc=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
