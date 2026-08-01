# Meowtoko E7 Tool runtime notices

This package contains a frozen Python backend and its Python/native library dependencies. Exact resolved versions, license identifiers, and upstream source links are recorded in the packaged `manifest.json`. Applicable license files collected from the exact build environment are under `licenses/`.

Core frozen dependencies include CPython, the PyInstaller bootloader, NumPy,
OpenCV, Pillow, pytesseract, Requests, Scapy, and their transitive runtime
dependencies. Their upstream licenses remain applicable; this notice does not
replace those licenses.

## Packet capture components

The frozen backend includes Scapy 2.7.0 for bounded local packet capture.
Scapy is published under GPLv2; its applicable license text and package
metadata are included with the packaged runtime notices. Upstream project:
<https://scapy.net/>.

Npcap is a separate system driver and is not included in the Meowtoko E7 Tool package.
Health Center opens the official Npcap download page for the user. The Npcap
Free Edition is not open-source software, limits ordinary free installation,
and does not grant third-party redistribution rights. Meowtoko E7 Tool does not download,
bundle, silently install, or redistribute it. Current license and download
terms: <https://npcap.com/>.

## Epic Seven and Fribbels-derived catalog data

Meowtoko E7 Tool is a free, unofficial fan-made companion and is not affiliated with or
endorsed by the Epic Seven rights holders. Epic Seven names, characters,
statistics, and other game material remain the property of their respective
rights holders. Public catalog facts are used with attribution under the
project owner's stated fair-use basis; this notice does not make a legal
guarantee.

The offline hero/artifact snapshot and `gear.txt` compatibility derive from
RexQian's Fribbels Epic 7 Optimizer offline fork at revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb` (declared app version
`1.11.0-offline.20260108`). The source project identifies Fribbels as author,
RexQian maintains the offline fork, and its `app/package.json` declares MIT;
the pinned root tree did not include a standalone license text. Snapshot input
SHA-256 values are
`a5ed0b641e578a2b290b75d6f75a866a93b91e40c1064a4f1a264630a745c349`
for 386 heroes and
`ed1bb666ae7465560fbc1a163000966821174b0a48be826b28da16021f463ac0`
for 283 artifacts. Full lineage ships with the catalog manifest and is recorded
in the source repository's `src/optimizer/data/CHARACTER_SNAPSHOT.md`.

The six Optimizer equipment-slot icons and 24 equipment-set icons are copied
without modification from Fribbels Epic 7 Optimizer revision
`b291cbbc415f11abede146859edc7b67d26e9c4b`, under `app/assets/`, using the
mapping declared by `app/js/lib/assets.js`. The upstream `app/package.json`
declares MIT. Per-file provenance is retained in
`assets/equipment/slots/SOURCE.md` and
`assets/equipment/sets/SOURCE.md`.

## Bundled CUDA installer helper

The package includes an application-local installer helper solely for the
optional pinned CUDA component. It contains the official 64-bit CPython
3.12.10 embeddable distribution (PSF-2.0) and pip 26.1.2 (MIT), including
pip's vendored license files. The exact upstream archive URLs, SHA-256 hashes,
file inventory, and per-file hashes are in
`../cuda-installer/asset-manifest.json`. It never replaces or discovers a
system Python installation.

The helper is not the CUDA component. CuPy and NVIDIA wheels remain outside
the standard package and are downloaded from the fixed PyPI index only after
the user confirms the large optional installation in Health Center. The
adjacent `component-requirements.txt` records the complete 11-package graph at
exact versions. Its SHA-256 and package list are recorded in the helper
manifest, and setup disables dependency resolution with `--no-deps`.

## External runtimes and executables

Meowtoko E7 Tool does **not** currently redistribute these executables:

- **Ollama** remains an externally installed managed dependency. Upstream source and license: <https://github.com/ollama/ollama>.
- **Tesseract OCR** remains externally installed. Tesseract is Apache-2.0, but its project provides no official current Windows installer; a complete third-party Windows runtime also needs separately attributable native dependencies and trained data. This release does not copy an arbitrary local or third-party build. Official download status: <https://tesseract-ocr.github.io/tessdoc/Downloads.html>. The manual analyzer and unrelated workflows remain usable without OCR.
- **CuPy / NVIDIA CUDA components** remain outside the standard CPU-safe package. The optional sidecar pins `cupy-cuda13x[ctk]==14.1.1` and its NVIDIA component dependencies from PyPI. Health Center requires confirmation before this large download, installs only through the bundled trusted fixed helper, and does not install or modify the NVIDIA display driver. The component wheels and driver retain their own licenses and terms. Official installation guidance: <https://docs.cupy.dev/en/stable/install.html>.
- **Android platform-tools / ADB** remains externally installed or user-configured. The Android SDK License Agreement restricts redistribution except where a component's separate open-source license permits it: <https://developer.android.com/tools/releases/platform-tools>.
- **Npcap** remains externally installed. Meowtoko E7 Tool opens the official download
  page but does not include or redistribute the free installer. Its upstream
  license limits ordinary free installations and requires separate rights for
  redistribution: <https://npcap.com/>.

Keeping these executables external avoids silently redistributing incomplete native runtimes or accepting third-party SDK terms on the user's behalf. The Health Center reports a missing executable as an isolated, repairable capability; unrelated workflows remain usable.
