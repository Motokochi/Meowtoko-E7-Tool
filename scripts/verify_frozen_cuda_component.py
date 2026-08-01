"""Prove that the frozen backend can use an already-installed CUDA sidecar."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from verify_frozen_backend import FrozenBackend


def run(executable: Path, user_data: Path, resources: Path) -> dict[str, object]:
    if not executable.is_file():
        raise AssertionError(f"Frozen backend is missing: {executable}")
    if not (user_data / "components" / "cuda-cupy13").is_dir():
        raise AssertionError("The isolated user-data root does not expose the CUDA component.")

    session = FrozenBackend(
        executable,
        user_data,
        resources,
        cuda_disabled=False,
    )
    try:
        health = session.request("health.get")
        deadline = time.monotonic() + 60
        while health["overall"] == "checking":
            if time.monotonic() >= deadline:
                raise AssertionError("Frozen CUDA health check did not finish within 60 seconds.")
            time.sleep(0.05)
            health = session.request("health.get")

        cuda = next(item for item in health["capabilities"] if item["id"] == "cuda")
        metadata = cuda["metadata"]
        component = metadata["component"]
        if cuda["state"] != "ready":
            raise AssertionError(
                f"Frozen CUDA capability is not ready: {cuda.get('detail') or cuda.get('summary')}"
            )
        if metadata["mode"] != "cuda" or not metadata["allocationProbeSucceeded"]:
            raise AssertionError("Frozen CUDA did not select GPU mode with a successful allocation probe.")
        if not component["installed"]:
            raise AssertionError("Frozen health did not accept the exact installed component revision.")
        device_name = str(metadata["deviceName"])
        if "RTX 5090" not in device_name:
            raise AssertionError(f"Unexpected CUDA device: {device_name}")
        print(
            "E7_FROZEN_CUDA_OK "
            f"device={device_name!r} cupy={cuda['version']} "
            f"runtime={metadata['runtimeVersion']} driver={metadata['driverVersion']} "
            f"probeBytes={metadata['allocationProbeBytes']}"
        )
        return cuda
    finally:
        session.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("user_data", type=Path)
    parser.add_argument("resources", type=Path)
    arguments = parser.parse_args()
    run(
        arguments.executable.resolve(),
        arguments.user_data.resolve(),
        arguments.resources.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
