import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scripts.build_packaged_character_assets import manifest_sha256, validate_visible_image
from scripts.download_e7codex_character_assets import (
    Character,
    DownloadTask,
    PNG_SIGNATURE,
    _catalog_characters,
    _download_task,
    _normalized_text_sha256,
)


class CharacterAssetPipelineTests(unittest.TestCase):
    def test_manifest_hash_is_stable_across_checkout_line_endings(self) -> None:
        lf = b'{\n  "schemaVersion": 1\n}\n'

        self.assertEqual(manifest_sha256(lf), manifest_sha256(lf.replace(b"\n", b"\r\n")))

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_bytes(lf.replace(b"\n", b"\r\n"))
            self.assertEqual(manifest_sha256(lf), _normalized_text_sha256(path))

    def test_manual_heroes_are_part_of_the_asset_catalog(self) -> None:
        root = Path(__file__).resolve().parents[1]
        characters = _catalog_characters(
            root / "src/optimizer/data/character_data/character-source-v1.json",
            root / "src/optimizer/data/character_data/manual-heroes-v1.json",
            {"c2186", "c5112"},
        )

        self.assertEqual(
            [
                ("c2186", "Lisette"),
                ("c5112", "Uncharted Pioneer Politis"),
            ],
            [(item.code, item.name) for item in characters],
        )

    def test_revisioned_asset_falls_back_to_the_base_asset_on_404(self) -> None:
        character = Character("c1234", "c1234", "Example", "example", "Example")
        with tempfile.TemporaryDirectory() as temporary:
            task = DownloadTask(
                character,
                "pose",
                ("https://example/c1234_1/pose.png", "https://example/c1234/pose.png"),
                Path(temporary) / "pose.png",
            )
            png = PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1, 1)
            calls = []

            def download(url: str, _timeout: float) -> bytes:
                calls.append(url)
                if "c1234_1" in url:
                    raise urllib.error.HTTPError(url, 404, "missing", {}, None)
                return png

            with patch("scripts.download_e7codex_character_assets._download_bytes", download):
                result = _download_task(task, force=True, retries=1, timeout=1)

        self.assertEqual(calls, list(task.source_urls))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["sourceUrl"], task.source_urls[-1])

    def test_fully_transparent_artwork_is_rejected(self) -> None:
        image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))

        with self.assertRaisesRegex(RuntimeError, "fully transparent"):
            validate_visible_image(image, "Example/pose.png")

    def test_visible_artwork_is_accepted(self) -> None:
        image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        image.putpixel((2, 2), (255, 255, 255, 255))

        validate_visible_image(image, "Example/pose.png")


if __name__ == "__main__":
    unittest.main()
