"""Generate the pinned offline character bundle from explicit Fribbels inputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.optimizer.data.character_snapshot import (  # noqa: E402
    BUNDLED_ARTIFACT_SOURCE_PATH,
    BUNDLED_CATALOG_FILENAME,
    BUNDLED_HERO_SOURCE_PATH,
    BUNDLED_SOURCE_FILENAME,
    BUNDLED_VALIDATION_FILENAME,
    build_character_snapshot,
    create_character_snapshot_manifest,
)


def _relative_output(value: str, option: str) -> str:
    if "\\" in value:
        raise ValueError(f"{option} must use forward slashes")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{option} must be a normalized relative path")
    return path.as_posix()


def _output_path(root: Path, relative_path: str) -> Path:
    return root.joinpath(*PurePosixPath(relative_path).parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize exact Fribbels hero/artifact cache bytes into the offline bundle."
    )
    parser.add_argument("--heroes-input", type=Path, required=True)
    parser.add_argument("--artifacts-input", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--catalog-output", required=True)
    parser.add_argument("--source-output", required=True)
    parser.add_argument("--validation-output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--vendored-heroes-output", required=True)
    parser.add_argument("--vendored-artifacts-output", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--fetched-at", required=True)
    parser.add_argument(
        "--allow-unpinned-inputs",
        action="store_true",
        help="Development-only: emit a report for altered fixtures without enforcing pinned hashes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_root = args.output_root.resolve()
    if {"user_data", "user-data"} & {part.lower() for part in output_root.parts}:
        raise ValueError("Character snapshots must not be written beneath local user data")

    relative_paths = {
        BUNDLED_CATALOG_FILENAME: _relative_output(args.catalog_output, "--catalog-output"),
        BUNDLED_SOURCE_FILENAME: _relative_output(args.source_output, "--source-output"),
        BUNDLED_VALIDATION_FILENAME: _relative_output(args.validation_output, "--validation-output"),
        BUNDLED_HERO_SOURCE_PATH: _relative_output(
            args.vendored_heroes_output, "--vendored-heroes-output"
        ),
        BUNDLED_ARTIFACT_SOURCE_PATH: _relative_output(
            args.vendored_artifacts_output, "--vendored-artifacts-output"
        ),
    }
    manifest_relative = _relative_output(args.manifest_output, "--manifest-output")
    all_relative = [*relative_paths.values(), manifest_relative]
    if len(all_relative) != len(set(all_relative)):
        raise ValueError("Every output path must be distinct")

    hero_bytes = args.heroes_input.resolve().read_bytes()
    artifact_bytes = args.artifacts_input.resolve().read_bytes()
    bundle = build_character_snapshot(
        hero_bytes,
        artifact_bytes,
        generated_at=args.generated_at,
        fetched_at=args.fetched_at,
        require_pinned_hashes=not args.allow_unpinned_inputs,
    )
    manifest = create_character_snapshot_manifest(bundle, relative_paths=relative_paths)
    generated = bundle.generated_bytes()
    contents = {
        relative_paths[BUNDLED_CATALOG_FILENAME]: generated[BUNDLED_CATALOG_FILENAME],
        relative_paths[BUNDLED_SOURCE_FILENAME]: generated[BUNDLED_SOURCE_FILENAME],
        relative_paths[BUNDLED_VALIDATION_FILENAME]: generated[BUNDLED_VALIDATION_FILENAME],
        relative_paths[BUNDLED_HERO_SOURCE_PATH]: hero_bytes,
        relative_paths[BUNDLED_ARTIFACT_SOURCE_PATH]: artifact_bytes,
        manifest_relative: manifest.to_json().encode("utf-8"),
    }
    for relative_path, content in contents.items():
        destination = _output_path(output_root, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    summary = bundle.validation_report.summary
    print(
        "CHARACTER_SNAPSHOT_OK "
        f"source={summary['sourceRecords']} normalized={summary['normalizedRecords']} "
        f"rejected={summary['rejectedRecords']} heroes={summary['canonicalHeroes']} "
        f"profiles={summary['canonicalProfiles']} artifacts={summary['canonicalArtifacts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
