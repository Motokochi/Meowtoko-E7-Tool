# Installing Meowtoko E7 Tool on Windows

Meowtoko E7 Tool is distributed as a normal desktop installer. You do not need to open PowerShell, install Python, or start the backend yourself.

## Install

1. Open the
   [latest release](https://github.com/Motokochi/Meowtoko-E7-Tool/releases/latest) and
   download its versioned `Setup.exe` asset.
2. Double-click the installer.
3. Because this free build is unsigned, Windows may show **Windows protected your PC**. Choose **More info**, verify that the app name is **Meowtoko E7 Tool**, and choose **Run anyway** only if you downloaded it from the official release.
4. Start **Meowtoko E7 Tool** from its desktop or Start menu shortcut. The app checks its backend and optional tools as it opens.

On first launch, open **Health Center** and confirm the packaged backend is
ready. Missing Npcap, Ollama, Tesseract, ADB, or GPU support affects only the
feature that needs it; CPU optimization and other independent workflows remain
usable. Continue with the [new-user workflow](USER_GUIDE.md).

Some PCs managed by an organization, or PCs with Smart App Control set to
**On**, refuse to run the installed unsigned executables instead of offering
**Run anyway**. There is no per-app Smart App Control exception. Meowtoko E7 Tool does
not weaken or bypass this protection. Either turn Smart App Control off in
**Windows Security → App & browser control → Smart App Control settings**,
use a machine whose policy allows unsigned apps, or install a future
trusted-signed release. Turning the protection back on will block the current
unsigned release again.

## Optional external tools

The installer includes the Meowtoko E7 Tool interface, frozen Python backend, Scapy
packet-capture client, complete character/artifact snapshot, schema authorities, and
the narrowly scoped optional-GPU installer helper. It does not require system
Python or Node.js.

Npcap, Ollama, Tesseract OCR, and Android Platform Tools (ADB) remain separate
optional installations. Npcap is needed for live game-data import and exact
Enhancer stat reads. Health Center opens its official graphical installer; E7
Hub does not redistribute Npcap or run a silent installer. Installing its
system driver may prompt for Windows administrator approval. Restart Meowtoko E7 Tool
after installation.

Tesseract's project does not publish an official current Windows installer, so
Meowtoko E7 Tool deliberately does not copy an arbitrary third-party or local build. The
in-app health screen reports what is missing; features that do not need a
missing tool remain usable. Manual Analyzer input does not require Tesseract.

## CPU and optional NVIDIA GPU modes

The normal app and optimizer are fully usable in CPU mode. CPU setup never
downloads or imports CuPy or NVIDIA components, and missing GPU support never
blocks gear import, profile editing, result browsing, or the other app tools.

Health Center uses the bounded, read-only NVIDIA `nvidia-smi` diagnostic when
it is available. Detecting an NVIDIA or RTX name only makes the optional setup
path visible; it does not claim CUDA is ready. Readiness still requires the
pinned component to load, a CUDA 13-compatible driver/runtime, a queryable
device, and a successful bounded allocation probe.

GPU-capable package builds offer **Install GPU components** on the GPU
acceleration card. Before installation, Meowtoko E7 Tool confirms that the fixed
`cupy-cuda13x[ctk]==14.1.1` component comes from PyPI, can download more than
1 GB, and can occupy several GB. A compatible NVIDIA driver is required, but
a system CUDA Toolkit and `nvcc` are not. Meowtoko E7 Tool does not install or modify the
display driver.

The operation runs without a PowerShell window and can be cancelled. A failed
or cancelled setup leaves CPU mode available. If an app-owned component is
present but cannot load, Health Center offers an atomic **Repair GPU
components** action. Package builds use only their bundled, hash-inventoried
CPython 3.12.10 + pip 26.1.2 helper and never search for system Python. The
helper is not CuPy: CuPy and NVIDIA wheels remain outside the standard package
and are downloaded only after confirmation. The helper uses an embedded
11-package, SHA-256-pinned requirements graph with pip dependency resolution
disabled, so a later upload cannot silently add or select another dependency.
GPU kernel-cache files are also kept under Meowtoko E7 Tool's preserved app-data folder,
not the account-wide `.cupy` directory.

After repairing an already loaded GPU component, close and reopen Meowtoko E7 Tool before
judging the final CUDA status. Windows can keep native `.pyd` files loaded until
the backend exits; the repair transaction preserves the newly published
component and removes any stale app-owned backup safely on a later startup.

## Uninstall and reinstall

Uninstall **Meowtoko E7 Tool** from Windows **Installed apps**. Uninstall removes the application and its shortcuts but intentionally preserves settings under the compatibility path `%APPDATA%\E7 Hub`, so upgrades from E7 Hub and later reinstalls keep your preferences. Delete that folder manually only if you also want to reset all Meowtoko E7 Tool settings.

The optional GPU component is stored with other preserved app data under that
folder. Use Health Center repair for a broken component. Delete the Meowtoko E7 Tool app
data folder only when you intentionally want to remove settings, optimizer
data, and optional components together.

This unsigned package does not install a certificate or modify Windows security settings.

## Data recovery

Updates and reinstalls preserve app-owned data. If settings, inventory,
profiles, results, or the optional GPU component need recovery, follow
[`RECOVERY.md`](RECOVERY.md). Stop Meowtoko E7 Tool first, preserve a separate copy, and
restore only the affected validated backup. Do not delete the entire
`%APPDATA%\E7 Hub` directory as a routine repair step.
