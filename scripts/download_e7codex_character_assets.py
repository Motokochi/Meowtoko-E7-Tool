"""Download and verify the base character artwork published by E7 Codex.

The downloader uses the pinned Meowtoko E7 Tool character snapshot as the source of
truth for display names and character codes. Files are stored under readable
character-name folders with stable local filenames and a machine-readable
manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = (
    REPOSITORY_ROOT
    / "src"
    / "optimizer"
    / "data"
    / "character_data"
    / "character-source-v1.json"
)
DEFAULT_MANUAL_HEROES = (
    REPOSITORY_ROOT
    / "src"
    / "optimizer"
    / "data"
    / "character_data"
    / "manual-heroes-v1.json"
)
DEFAULT_DESTINATION = (
    REPOSITORY_ROOT / ".build" / "downloads" / "e7codex-characters"
)
DEFAULT_BASE_URL = "https://e7codex.com/assets"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 96 * 1024 * 1024
VARIANTS = ("pose", "face_l", "face_s", "face_su")
# Fribbels and E7 Codex do not always use the same record identifier. Keep the
# optimizer-facing code stable while resolving the public E7 Codex asset code.
ASSET_CODE_OVERRIDES = {
    "c5004": "m9194",  # Archdemon's Shadow -> Archdemon Mercedes
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class Character:
    code: str
    asset_code: str
    name: str
    slug: str
    folder: str


@dataclass(frozen=True, slots=True)
class DownloadTask:
    character: Character
    variant: str
    source_urls: tuple[str, str]
    destination: Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download E7 Codex pose and face images for the pinned Meowtoko E7 Tool catalog.",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manual-heroes", type=Path, default=DEFAULT_MANUAL_HEROES)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--codes",
        nargs="*",
        help="Optional character-code subset used for focused verification.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return a failure code when E7 Codex does not publish every requested image.",
    )
    return parser.parse_args()


def _safe_folder_name(name: str, code: str) -> str:
    value = unicodedata.normalize("NFC", name)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " - ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = code
    if value.upper() in WINDOWS_RESERVED_NAMES:
        value = f"{value} ({code})"
    return value


def _catalog_characters(
    catalog_path: Path,
    manual_heroes_path: Path,
    requested_codes: set[str] | None,
) -> list[Character]:
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    records = document.get("records", {}).get("heroes")
    if not isinstance(records, dict):
        raise ValueError(f"{catalog_path} does not contain records.heroes.")
    manual_document = json.loads(manual_heroes_path.read_text(encoding="utf-8"))
    manual_records = manual_document.get("records")
    if not isinstance(manual_records, dict):
        raise ValueError(f"{manual_heroes_path} does not contain records.")
    records = {**records, **manual_records}

    pending: list[tuple[str, str, str]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        code = record.get("code")
        name = record.get("name")
        slug = record.get("_id")
        if not all(isinstance(value, str) and value.strip() for value in (code, name, slug)):
            raise ValueError("Every catalog hero must have a non-empty code, name, and _id.")
        if requested_codes is None or code in requested_codes:
            pending.append((name, code, slug))

    pending.sort(key=lambda item: (item[0].casefold(), item[1]))
    used: dict[str, str] = {}
    characters: list[Character] = []
    for name, code, slug in pending:
        folder = _safe_folder_name(name, code)
        collision_key = folder.casefold()
        if collision_key in used and used[collision_key] != code:
            folder = f"{folder} ({code})"
            collision_key = folder.casefold()
        used[collision_key] = code
        characters.append(
            Character(
                code=code,
                asset_code=ASSET_CODE_OVERRIDES.get(code, code),
                name=name,
                slug=slug,
                folder=folder,
            )
        )

    if requested_codes is not None:
        found = {character.code for character in characters}
        missing_codes = sorted(requested_codes - found)
        if missing_codes:
            raise ValueError(f"Unknown character codes: {', '.join(missing_codes)}")
    return characters


def _source_filename(code: str, variant: str) -> str:
    if variant == "pose":
        return "pose.png"
    suffix = variant.removeprefix("face_")
    return f"face_{code}_{suffix}.png"


def _local_filename(variant: str) -> str:
    return f"{variant}.png"


def _png_dimensions(header: bytes) -> tuple[int, int]:
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        raise ValueError("Response is not a valid PNG with an IHDR header.")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive.")
    return width, height


def _file_metadata(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    header = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if len(header) < 24:
                header += chunk[: 24 - len(header)]
            digest.update(chunk)
            size += len(chunk)
    width, height = _png_dimensions(header)
    return {
        "bytes": size,
        "sha256": digest.hexdigest(),
        "width": width,
        "height": height,
    }


def _download_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/png,image/*;q=0.8",
            "User-Agent": "Meowtoko-E7-Tool-character-asset-sync/1.0 (+https://github.com/Motokochi/Meowtoko-E7-Tool)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        if not content_type.startswith("image/"):
            raise ValueError(f"Unexpected content type {content_type!r}.")
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image exceeds the {MAX_IMAGE_BYTES}-byte safety limit.")
    _png_dimensions(data[:24])
    return data


def _download_task(
    task: DownloadTask,
    *,
    force: bool,
    retries: int,
    timeout: float,
) -> dict[str, object]:
    if task.destination.is_file() and not force:
        try:
            return {
                "status": "available",
                "reused": True,
                "sourceUrl": task.source_urls[0],
                **_file_metadata(task.destination),
            }
        except (OSError, ValueError):
            pass

    for source_url in task.source_urls:
        failure: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                data = _download_bytes(source_url, timeout)
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = task.destination.with_name(
                    f".{task.destination.name}.{os.getpid()}.{threading.get_ident()}.part"
                )
                try:
                    temporary.write_bytes(data)
                    os.replace(temporary, task.destination)
                finally:
                    temporary.unlink(missing_ok=True)
                return {
                    "status": "available",
                    "reused": False,
                    "sourceUrl": source_url,
                    **_file_metadata(task.destination),
                }
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    break
                failure = error
            except (OSError, TimeoutError, urllib.error.URLError, ValueError) as error:
                failure = error
            if attempt < retries:
                time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
        if failure is not None:
            return {
                "status": "error",
                "reused": False,
                "sourceUrl": source_url,
                "message": f"{type(failure).__name__}: {failure}",
            }

    return {
        "status": "missing",
        "reused": False,
        "sourceUrl": task.source_urls[-1],
        "httpStatus": 404,
        "message": "E7 Codex does not publish this image.",
    }


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_index(path: Path, characters: Iterable[dict[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=(
                    "name",
                    "code",
                    "asset_code",
                    "folder",
                    "available",
                    "missing",
                    "errors",
                    "bytes",
                ),
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            for character in characters:
                files = character["files"]
                statuses = [file["status"] for file in files.values()]
                available_bytes = sum(
                    int(file.get("bytes", 0))
                    for file in files.values()
                    if file["status"] == "available"
                )
                writer.writerow({
                    "name": character["name"],
                    "code": character["code"],
                    "asset_code": character["assetCode"],
                    "folder": character["folder"],
                    "available": statuses.count("available"),
                    "missing": statuses.count("missing"),
                    "errors": statuses.count("error"),
                    "bytes": available_bytes if statuses.count("available") else "",
                })
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(arguments: argparse.Namespace) -> int:
    catalog_path = arguments.catalog.resolve()
    manual_heroes_path = arguments.manual_heroes.resolve()
    destination = arguments.destination.resolve()
    if arguments.workers < 1 or arguments.workers > 24:
        raise ValueError("--workers must be between 1 and 24.")
    if arguments.retries < 1 or arguments.retries > 8:
        raise ValueError("--retries must be between 1 and 8.")
    if arguments.timeout <= 0 or arguments.timeout > 180:
        raise ValueError("--timeout must be greater than zero and at most 180 seconds.")

    requested_codes = set(arguments.codes) if arguments.codes else None
    characters = _catalog_characters(catalog_path, manual_heroes_path, requested_codes)
    base_url = arguments.base_url.rstrip("/")
    tasks = [
        DownloadTask(
            character=character,
            variant=variant,
            source_urls=(
                f"{base_url}/{character.asset_code}_1/{_source_filename(character.asset_code, variant)}",
                f"{base_url}/{character.asset_code}/{_source_filename(character.asset_code, variant)}",
            ),
            destination=destination / character.folder / _local_filename(variant),
        )
        for character in characters
        for variant in VARIANTS
    ]

    print(
        f"E7_CODEX_ASSET_SYNC_START characters={len(characters)} "
        f"files={len(tasks)} workers={arguments.workers} destination={destination}",
        flush=True,
    )
    results: dict[tuple[str, str], dict[str, object]] = {}
    progress_lock = threading.Lock()
    completed = 0
    with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
        futures = {
            executor.submit(
                _download_task,
                task,
                force=arguments.force,
                retries=arguments.retries,
                timeout=arguments.timeout,
            ): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            result.update({
                "path": f"{task.character.folder}/{_local_filename(task.variant)}",
            })
            results[(task.character.code, task.variant)] = result
            with progress_lock:
                completed += 1
                if completed % 25 == 0 or completed == len(tasks):
                    available = sum(
                        item["status"] == "available" for item in results.values()
                    )
                    missing = sum(item["status"] == "missing" for item in results.values())
                    errors = sum(item["status"] == "error" for item in results.values())
                    print(
                        f"E7_CODEX_ASSET_SYNC_PROGRESS completed={completed}/{len(tasks)} "
                        f"available={available} missing={missing} errors={errors}",
                        flush=True,
                    )

    character_entries: list[dict[str, object]] = []
    for character in characters:
        character_entries.append({
            "name": character.name,
            "code": character.code,
            "assetCode": character.asset_code,
            "slug": character.slug,
            "folder": character.folder,
            "files": {
                variant: results[(character.code, variant)]
                for variant in VARIANTS
            },
        })

    statuses = [
        file["status"]
        for character in character_entries
        for file in character["files"].values()
    ]
    total_bytes = sum(
        int(file.get("bytes", 0))
        for character in character_entries
        for file in character["files"].values()
        if file["status"] == "available"
    )
    catalog_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    manifest = {
        "schemaId": "e7hub.e7codex-character-assets",
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": {
            "site": "https://e7codex.com/",
            "assetBaseUrl": base_url,
            "catalogPath": catalog_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "catalogSha256": catalog_hash,
            "manualHeroesPath": manual_heroes_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "manualHeroesSha256": hashlib.sha256(manual_heroes_path.read_bytes()).hexdigest(),
        },
        "summary": {
            "characters": len(character_entries),
            "expectedFiles": len(tasks),
            "availableFiles": statuses.count("available"),
            "missingFiles": statuses.count("missing"),
            "errorFiles": statuses.count("error"),
            "totalBytes": total_bytes,
        },
        "characters": character_entries,
    }
    _atomic_json(destination / "asset-manifest.json", manifest)
    _write_index(destination / "index.csv", character_entries)

    print(
        "E7_CODEX_ASSET_SYNC_OK "
        f"characters={len(character_entries)} available={statuses.count('available')} "
        f"missing={statuses.count('missing')} errors={statuses.count('error')} "
        f"bytes={total_bytes}",
        flush=True,
    )
    if statuses.count("error"):
        return 1
    if arguments.require_complete and statuses.count("missing"):
        return 2
    return 0


def main() -> int:
    try:
        return run(parse_arguments())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"E7_CODEX_ASSET_SYNC_FAILED {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
