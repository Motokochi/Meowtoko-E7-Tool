import unittest

from PIL import Image

from scripts.build_packaged_character_assets import validate_visible_image
from scripts.download_e7codex_character_assets import ASSET_DIRECTORY_OVERRIDES


class CharacterAssetPipelineTests(unittest.TestCase):
    def test_revisioned_e7_codex_asset_directories_are_pinned(self) -> None:
        self.assertEqual(
            ASSET_DIRECTORY_OVERRIDES,
            {
                "c1180": "c1180_1",
                "c1183": "c1183_1",
                "c2076": "c2076_1",
                "c2148": "c2148_1",
                "c2181": "c2181_1",
                "c2184": "c2184_1",
                "c2185": "c2185_1",
                "c5069": "c5069_1",
                "c5147": "c5147_1",
                "c5190": "c5190_1",
                "c6024": "c6024_1",
            },
        )

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
