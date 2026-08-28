from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class RepositoryLayoutTests(unittest.TestCase):
    def test_packaging_inputs_use_semantic_locations(self) -> None:
        spec = ROOT / "packaging" / "e7-core.spec"
        build_script = (ROOT / "desktop" / "scripts" / "build-backend.cjs").read_text(
            encoding="utf-8"
        )
        runtime_export = (ROOT / "scripts" / "export_runtime_metadata.py").read_text(
            encoding="utf-8"
        )

        self.assertTrue(spec.is_file())
        self.assertIn("ROOT = Path(SPECPATH).resolve().parent", spec.read_text(encoding="utf-8"))
        self.assertIn("'packaging', 'e7-core.spec'", build_script)
        self.assertIn('ROOT / "docs" / "legal" / "THIRD_PARTY_NOTICES.md"', runtime_export)
        self.assertIn('destination / "THIRD_PARTY_NOTICES.md"', runtime_export)
        self.assertIn(
            'requirements_pins(ROOT / "requirements-core.txt")',
            runtime_export,
        )
        self.assertNotIn("RUNTIME_PACKAGES", runtime_export)
        self.assertIn('"lockSha256": normalized_text_sha256(', runtime_export)
        self.assertIn("'.build', 'pyinstaller', 'e7-core'", build_script)
        self.assertIn("['-3.12']", build_script)
        self.assertNotIn("['-3.13']", build_script)

        for retired in ("e7_core.spec", "e7_hub.spec", "e7_hub.py"):
            with self.subTest(retired=retired):
                self.assertFalse((ROOT / retired).exists())

    def test_generated_and_local_defaults_use_semantic_roots(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        cuda_builder = (ROOT / "scripts" / "build_cuda_installer.py").read_text(
            encoding="utf-8"
        )
        desktop_tests = (ROOT / "desktop" / "tsconfig.tests.json").read_text(
            encoding="utf-8"
        )
        forge = (ROOT / "desktop" / "forge.config.ts").read_text(encoding="utf-8")
        workspace_paths = (ROOT / "src" / "core" / "workspace_paths.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("/.build/", ignore)
        self.assertIn("/.local/", ignore)
        self.assertIn('ROOT / ".build" / "downloads"', cuda_builder)
        self.assertIn("../.build/desktop-tests", desktop_tests)
        self.assertIn("'.build', 'forge'", forge)
        self.assertIn('Path(".local") / "user-data"', workspace_paths)

    def test_ignore_and_cleanup_contracts_are_bounded(self) -> None:
        ignore_lines = [
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        package = (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
        cleanup = (ROOT / "scripts" / "cleanup-contract.cjs").read_text(encoding="utf-8")

        self.assertEqual(len(ignore_lines), len(set(ignore_lines)))
        for expected in (
            "/.idea/",
            "/.pnpm-store/",
            "/.build/",
            "/.local/",
            "/benchmarks/",
            "/phases/",
            "/dist/",
            "/releases/",
            "/desktop/node_modules/",
            "/desktop/.webpack/",
            "__pycache__/",
            "*.log",
            "*.db",
            "*.result-store",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, ignore_lines)

        self.assertIn('"cleanup:build": "node ../scripts/cleanup-build-output.cjs"', package)
        self.assertIn(
            '"cleanup:releases": "node ../scripts/cleanup-release-archives.cjs"',
            package,
        )
        self.assertIn("Cleanup must never target the repository root.", cleanup)
        self.assertIn("Cleanup target must stay within the repository", cleanup)
        self.assertIn("Cleanup target overlaps installed application data", cleanup)
        self.assertIn("PRESERVED_RELEASE", cleanup)

    def test_public_character_assets_use_the_compact_verified_baseline(self) -> None:
        character_root = ROOT / "assets" / "characters"
        manifest = (character_root / "asset-manifest.json").read_text(encoding="utf-8")
        raw_manifest = character_root / "raw-source-manifest.json"
        artwork = list(character_root.rglob("*.webp"))

        self.assertTrue(raw_manifest.is_file())
        self.assertEqual(len(artwork), 1536)
        self.assertFalse(any(character_root.rglob("*.png")))
        self.assertIn('"format": "webp"', manifest)
        self.assertIn('"sourceManifestSha256"', manifest)
        self.assertLess(sum(path.stat().st_size for path in artwork), 120 * 1024 * 1024)

        downloader = (
            ROOT / "scripts" / "download_e7codex_character_assets.py"
        ).read_text(encoding="utf-8")
        builder = (
            ROOT / "scripts" / "build_packaged_character_assets.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'REPOSITORY_ROOT / ".build" / "downloads" / "e7codex-characters"',
            downloader,
        )
        self.assertIn("validate_prepackaged_source", builder)

    def test_moved_documentation_has_no_broken_local_links(self) -> None:
        documents = [ROOT / "README.md"]
        documents.extend((ROOT / "docs").rglob("*.md"))

        for document in documents:
            content = document.read_text(encoding="utf-8")
            for match in MARKDOWN_LINK.finditer(content):
                target = match.group(1).strip().strip("<>")
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                relative = target.split("#", 1)[0]
                with self.subTest(document=document.relative_to(ROOT), target=target):
                    self.assertTrue((document.parent / relative).resolve().exists())


if __name__ == "__main__":
    unittest.main()
