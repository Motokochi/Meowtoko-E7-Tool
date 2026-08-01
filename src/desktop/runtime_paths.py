"""Runtime executable resolution for development and future packaged builds."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Callable


def _first_existing(
    candidates: list[str | None],
    *,
    exists: Callable[[str], bool],
) -> str | None:
    for candidate in candidates:
        if candidate and exists(candidate):
            return str(Path(candidate).resolve())
    return None


def resolve_tesseract_path(
    *,
    environment: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    env = os.environ if environment is None else environment
    resources = env.get("E7_RESOURCES_PATH")
    program_files = env.get("ProgramFiles") or r"C:\Program Files"
    candidates = [
        env.get("E7_TESSERACT_PATH"),
        str(Path(resources) / "tesseract" / "tesseract.exe") if resources else None,
        str(Path(program_files) / "Tesseract-OCR" / "tesseract.exe"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    ]
    return _first_existing(candidates, exists=exists) or which("tesseract")


def resolve_ollama_path(
    *,
    environment: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    env = os.environ if environment is None else environment
    local_app_data = env.get("LOCALAPPDATA")
    program_files = env.get("ProgramFiles")
    program_files_x86 = env.get("ProgramFiles(x86)")
    candidates = [
        env.get("E7_OLLAMA_PATH"),
        str(Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe") if local_app_data else None,
        str(Path(program_files) / "Ollama" / "ollama.exe") if program_files else None,
        str(Path(program_files_x86) / "Ollama" / "ollama.exe") if program_files_x86 else None,
    ]
    return which("ollama") or _first_existing(candidates, exists=exists)


def resolve_adb_path(
    configured: str,
    *,
    exists: Callable[[str], bool] = os.path.isfile,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    value = configured.strip() or "adb"
    if exists(value):
        return str(Path(value).resolve())
    return which(value)


def resolve_nvidia_smi_path(
    *,
    environment: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """Resolve NVIDIA's read-only diagnostic without depending on a CUDA toolkit."""

    env = os.environ if environment is None else environment
    resources = env.get("E7_RESOURCES_PATH")
    program_files = env.get("ProgramW6432") or env.get("ProgramFiles") or r"C:\Program Files"
    system_root = env.get("SystemRoot") or r"C:\Windows"
    candidates = [
        env.get("E7_NVIDIA_SMI_PATH"),
        str(Path(resources) / "nvidia" / "nvidia-smi.exe") if resources else None,
        str(Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
        str(Path(system_root) / "System32" / "nvidia-smi.exe"),
    ]
    return _first_existing(candidates, exists=exists) or which("nvidia-smi")
