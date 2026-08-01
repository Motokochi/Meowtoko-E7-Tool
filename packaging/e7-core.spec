# -*- mode: python ; coding: utf-8 -*-
"""Windowless, backend-only PyInstaller definition for the Electron package."""

from pathlib import Path


# The spec lives under packaging/, while every bundled source path remains
# repository-relative.
ROOT = Path(SPECPATH).resolve().parent

# Desktop workflows load their heavy optional dependencies lazily so a missing
# capability cannot prevent the rest of the backend from starting. PyInstaller
# cannot discover those imports statically, so keep this list explicit.
HIDDEN_IMPORTS = [
    "src.core.settings_service",
    "src.desktop.optimizer_profile_service",
    "src.optimizer.cuda.inputs",
    "src.optimizer.cuda.compaction",
    "src.optimizer.cuda.packed",
    "src.optimizer.cuda.orchestration",
    "src.optimizer.cuda.runtime",
    "src.optimizer.result_store.schema",
    "src.optimizer.result_store.adapters",
    "src.optimizer.result_store.filtering",
    "src.optimizer.result_store.indexing",
    "src.optimizer.result_store.resolution",
    "src.optimizer.result_store.lifecycle",
    "src.optimizer.result_store.exporting",
    "src.optimizer.result_store.storage",
    "src.optimizer.search.cartesian",
    "src.optimizer.search.cpu_orchestration",
    "src.optimizer.search.exact_evaluation",
    "src.optimizer.search.match_counting",
    "src.optimizer.search.set_patterns",
    "src.optimizer.search.slot_arrays",
    "src.optimizer.data.artifact_repository",
    "src.optimizer.data.inventory_repository",
    "src.optimizer.data.schemas",
    "src.core.enhancement_automator",
    "src.core.orchestrator",
    "src.extractors.candidates",
    "src.extractors.hybrid_parser",
    "src.extractors.llm_client",
    "src.extractors.ocr_engine",
    "src.optimizer.data.character_snapshot",
    "src.optimizer.data.character_repository",
    "src.optimizer.data.character_profiles",
    "src.optimizer.data.hero_modifier_repository",
    "src.optimizer.data.skill_context_repository",
    "src.optimizer.engine.derived_metrics",
    "src.optimizer.engine.primary_stat_bounds",
    "src.optimizer.engine.priority_scoring",
    "src.optimizer.engine.stat_aggregation",
    "src.optimizer.engine.set_evaluation",
    "src.utils.debugger",
    "src.vision.automation_backend",
    "src.vision.filters",
    "cv2",
    "numpy",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageEnhance",
    "PIL.ImageFilter",
    "PIL.ImageOps",
    "pytesseract",
    "requests",
    "scapy.all",
    # cuda-pathfinder is installed later as an optional sidecar. PyInstaller
    # cannot discover the stdlib modules that sidecar imports dynamically.
    "graphlib",
    "ctypes.wintypes",
]


analysis = Analysis(
    [str(ROOT / "src" / "desktop" / "backend.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (
            str(ROOT / "src" / "optimizer" / "data" / "character_data"),
            "src/optimizer/data/character_data",
        ),
    ],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "cupy",
        "cupyx",
        "cryptography",
        "dateutil",
        "google",
        "google_genai",
        "jinja2",
        "lxml",
        "openpyxl",
        "pandas",
        "pytest",
        "pip",
        "setuptools",
        "tests",
        "tkinter",
        "tzdata",
        "wheel",
        "nvidia",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="e7-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="backend",
)
