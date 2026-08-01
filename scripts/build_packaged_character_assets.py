"""Build compact, integrity-checked character artwork for desktop packaging."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from PIL import Image


SCHEMA_ID = "e7hub.e7codex-character-assets"
VARIANTS = ("pose", "face_l", "face_s", "face_su")
FORMAT = "webp"
QUALITY = 90
METHOD = 6
POSE_MAX_DIMENSION = 1600


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def packaging_metadata(source_manifest_sha256: str) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "quality": QUALITY,
        "method": METHOD,
        "poseMaxDimension": POSE_MAX_DIMENSION,
        "sourceManifestSha256": source_manifest_sha256,
    }


def existing_output_is_valid(
    output_root: Path,
    expected_packaging: dict[str, Any],
) -> bool:
    manifest_path = output_root / "asset-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schemaId") != SCHEMA_ID:
            return False
        if manifest.get("packaging") != expected_packaging:
            return False
        available = 0
        for character in manifest["characters"]:
            for variant in VARIANTS:
                record = character["files"][variant]
                if record.get("status") != "available":
                    continue
                available += 1
                relative = record["path"]
                candidate = (output_root / Path(*relative.split("/"))).resolve()
                if output_root.resolve() not in candidate.parents:
                    return False
                if candidate.suffix.lower() != ".webp":
                    return False
                if candidate.stat().st_size != record["bytes"]:
                    return False
                if file_sha256(candidate) != record["sha256"]:
                    return False
        return available == manifest["summary"]["availableFiles"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def validate_prepackaged_source(
    source_root: Path,
    manifest: dict[str, Any],
) -> None:
    packaging = manifest.get("packaging")
    if not isinstance(packaging, dict) or packaging.get("format") != FORMAT:
        raise RuntimeError("Prepackaged character artwork metadata is invalid.")
    if (
        packaging.get("quality") != QUALITY
        or packaging.get("method") != METHOD
        or packaging.get("poseMaxDimension") != POSE_MAX_DIMENSION
    ):
        raise RuntimeError("Prepackaged character artwork settings drifted.")

    raw_manifest = source_root / "raw-source-manifest.json"
    if file_sha256(raw_manifest) != packaging.get("sourceManifestSha256"):
        raise RuntimeError("Raw character source manifest hash drifted.")

    available = 0
    total_bytes = 0
    for character in manifest["characters"]:
        for variant in VARIANTS:
            record = character["files"][variant]
            if record.get("status") != "available":
                continue
            available += 1
            relative = record["path"]
            candidate = (source_root / Path(*relative.split("/"))).resolve()
            if source_root.resolve() not in candidate.parents:
                raise RuntimeError(f"Prepackaged artwork escapes its root: {relative}")
            if candidate.suffix.lower() != ".webp":
                raise RuntimeError(f"Prepackaged artwork is not WebP: {relative}")
            if candidate.stat().st_size != record["bytes"]:
                raise RuntimeError(f"Prepackaged artwork size drift: {relative}")
            if file_sha256(candidate) != record["sha256"]:
                raise RuntimeError(f"Prepackaged artwork hash drift: {relative}")
            if not isinstance(record.get("sourceFile"), dict):
                raise RuntimeError(f"Prepackaged artwork lost source provenance: {relative}")
            total_bytes += record["bytes"]
    if available != manifest["summary"]["availableFiles"]:
        raise RuntimeError("Prepackaged artwork file count drifted.")
    if total_bytes != manifest["summary"]["totalBytes"]:
        raise RuntimeError("Prepackaged artwork byte count drifted.")


def publish_prepackaged_source(
    source_root: Path,
    output_root: Path,
    manifest: dict[str, Any],
    manifest_bytes: bytes,
) -> None:
    packaging = manifest["packaging"]
    if (
        existing_output_is_valid(output_root, packaging)
        and file_sha256(output_root / "asset-manifest.json")
        == hashlib.sha256(manifest_bytes).hexdigest()
    ):
        print(
            "E7_PACKAGED_CHARACTER_ASSETS_OK "
            f"files={manifest['summary']['availableFiles']} "
            f"bytes={manifest['summary']['totalBytes']} reused=1"
        )
        return

    temporary_root = output_root.parent / f".{output_root.name}-{uuid.uuid4().hex}.tmp"
    temporary_root.mkdir(parents=True, exist_ok=False)
    try:
        for character in manifest["characters"]:
            for variant in VARIANTS:
                record = character["files"][variant]
                if record.get("status") != "available":
                    continue
                relative = Path(*record["path"].split("/"))
                destination = temporary_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_root / relative, destination)
        (temporary_root / "asset-manifest.json").write_bytes(manifest_bytes)
        if output_root.exists():
            shutil.rmtree(output_root)
        os.replace(temporary_root, output_root)
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise

    print(
        "E7_PACKAGED_CHARACTER_ASSETS_OK "
        f"files={manifest['summary']['availableFiles']} "
        f"bytes={manifest['summary']['totalBytes']} reused=0"
    )


def convert_file(task: tuple[int, str, str, str, dict[str, Any]]) -> tuple[int, str, dict[str, Any]]:
    character_index, variant, source_name, output_name, record = task
    source_path = Path(source_name)
    output_path = Path(output_name)
    if source_path.stat().st_size != record["bytes"]:
        raise RuntimeError(f"Source size drift: {record['path']}")
    if file_sha256(source_path) != record["sha256"]:
        raise RuntimeError(f"Source hash drift: {record['path']}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source_image:
        source_image.load()
        image = source_image
        if variant == "pose":
            image.thumbnail(
                (POSE_MAX_DIMENSION, POSE_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
        if image.mode not in {"RGB", "RGBA"}:
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
        image.save(
            output_path,
            FORMAT.upper(),
            quality=QUALITY,
            method=METHOD,
            exact=True,
        )

    with Image.open(output_path) as packaged_image:
        packaged_image.verify()
    with Image.open(output_path) as packaged_image:
        width, height = packaged_image.size

    packaged_record = copy.deepcopy(record)
    packaged_record.pop("reused", None)
    packaged_record["sourceFile"] = {
        "path": record["path"],
        "bytes": record["bytes"],
        "sha256": record["sha256"],
        "width": record["width"],
        "height": record["height"],
    }
    packaged_record.update(
        {
            "path": Path(output_name).name,
            "bytes": output_path.stat().st_size,
            "sha256": file_sha256(output_path),
            "width": width,
            "height": height,
        }
    )
    return character_index, variant, packaged_record


def build(source_root: Path, output_root: Path, workers: int) -> None:
    source_manifest_path = source_root / "asset-manifest.json"
    source_manifest_bytes = source_manifest_path.read_bytes()
    source_manifest_sha256 = hashlib.sha256(source_manifest_bytes).hexdigest()
    source_manifest = json.loads(source_manifest_bytes)
    if source_manifest.get("schemaId") != SCHEMA_ID or source_manifest.get("schemaVersion") != 1:
        raise RuntimeError("Source character artwork manifest is invalid.")

    if "packaging" in source_manifest:
        validate_prepackaged_source(source_root, source_manifest)
        publish_prepackaged_source(
            source_root,
            output_root,
            source_manifest,
            source_manifest_bytes,
        )
        return

    packaging = packaging_metadata(source_manifest_sha256)
    if existing_output_is_valid(output_root, packaging):
        manifest = json.loads((output_root / "asset-manifest.json").read_text(encoding="utf-8"))
        print(
            "E7_PACKAGED_CHARACTER_ASSETS_OK "
            f"files={manifest['summary']['availableFiles']} "
            f"bytes={manifest['summary']['totalBytes']} reused=1"
        )
        return

    temporary_root = output_root.parent / f".{output_root.name}-{uuid.uuid4().hex}.tmp"
    temporary_root.mkdir(parents=True, exist_ok=False)
    packaged_manifest = copy.deepcopy(source_manifest)
    packaged_manifest["packaging"] = packaging
    packaged_manifest["summary"]["sourceTotalBytes"] = source_manifest["summary"]["totalBytes"]

    tasks: list[tuple[int, str, str, str, dict[str, Any]]] = []
    output_relatives: dict[tuple[int, str], str] = {}
    try:
        for character_index, character in enumerate(source_manifest["characters"]):
            folder = character["folder"]
            for variant in VARIANTS:
                record = character["files"][variant]
                if record.get("status") != "available":
                    continue
                source_relative = record["path"]
                output_relative = str(
                    Path(folder, f"{variant}.webp")
                ).replace("\\", "/")
                source_path = source_root / Path(*source_relative.split("/"))
                output_path = temporary_root / Path(*output_relative.split("/"))
                tasks.append(
                    (
                        character_index,
                        variant,
                        str(source_path),
                        str(output_path),
                        record,
                    )
                )
                output_relatives[(character_index, variant)] = output_relative

        converted: dict[tuple[int, str], dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(convert_file, task) for task in tasks]
            for future in as_completed(futures):
                character_index, variant, record = future.result()
                record["path"] = output_relatives[(character_index, variant)]
                converted[(character_index, variant)] = record

        total_bytes = 0
        for character_index, character in enumerate(packaged_manifest["characters"]):
            for variant in VARIANTS:
                record = character["files"][variant]
                if record.get("status") != "available":
                    continue
                packaged_record = converted[(character_index, variant)]
                character["files"][variant] = packaged_record
                total_bytes += packaged_record["bytes"]

        packaged_manifest["summary"]["totalBytes"] = total_bytes
        (temporary_root / "asset-manifest.json").write_text(
            json.dumps(packaged_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if output_root.exists():
            shutil.rmtree(output_root)
        os.replace(temporary_root, output_root)
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise

    print(
        "E7_PACKAGED_CHARACTER_ASSETS_OK "
        f"files={packaged_manifest['summary']['availableFiles']} "
        f"bytes={packaged_manifest['summary']['totalBytes']} reused=0"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    build(args.source.resolve(), args.output.resolve(), args.workers)


if __name__ == "__main__":
    main()
