import base64
import io
import unittest

from PIL import Image

from src.core.settings_service import document_to_protocol, default_settings
from src.desktop.settings_preview import SettingsPreviewError, SettingsPreviewService


class FakeAdbBackend:
    def __init__(self, settings, calls):
        self.settings = settings
        self.calls = calls

    def capture_region(self, region):
        self.calls.append((self.settings, dict(region)))
        return Image.new("RGB", (110, 110), "navy")


class MissingAdbBackend:
    def capture_region(self, _region):
        return None


class SettingsPreviewTests(unittest.TestCase):
    def setUp(self):
        self.adb_calls = []
        self.service = SettingsPreviewService(
            adb_backend_factory=lambda settings: FakeAdbBackend(settings, self.adb_calls),
        )
        self.settings = document_to_protocol(default_settings())

    def test_adb_region_preview_uses_unsaved_typed_settings(self):
        self.settings["regions"]["mainStat"] = {"x": 8, "y": 9, "width": 120, "height": 44}

        result = self.service.preview(self.settings, {
            "source": "adb",
            "target": {"kind": "region", "id": "mainStat"},
        })

        self.assertEqual(self.adb_calls[0][1], {"x": 8, "y": 9, "width": 120, "height": 44})
        self.assertEqual(result["width"], 110)
        self.assertEqual(result["height"], 110)
        self.assertEqual(result["itemId"], "mainStat")
        self.assertTrue(result["dataUrl"].startswith("data:image/png;base64,"))

    def test_adb_click_preview_draws_crosshair_without_clicking(self):
        result = self.service.preview(self.settings, {
            "source": "adb",
            "target": {"kind": "point", "id": "destroy"},
        })

        self.assertEqual(len(self.adb_calls), 1)
        self.assertEqual(self.adb_calls[0][1], {"x": 291, "y": 625, "width": 110, "height": 110})
        payload = base64.b64decode(result["dataUrl"].split(",", 1)[1])
        image = Image.open(io.BytesIO(payload)).convert("RGB")
        self.assertEqual(image.getpixel((55, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((0, 55)), (255, 0, 0))

    def test_rejects_untyped_targets_oversized_regions_and_missing_captures(self):
        with self.assertRaisesRegex(SettingsPreviewError, "requires only"):
            self.service.preview(self.settings, {
                "source": "adb",
                "target": {"kind": "region", "id": "slot"},
                "shellCommand": "anything",
            })

        self.settings["regions"]["slot"] = {"x": 0, "y": 0, "width": 5000, "height": 20}
        with self.assertRaisesRegex(SettingsPreviewError, "4096"):
            self.service.preview(self.settings, {
                "source": "adb",
                "target": {"kind": "region", "id": "slot"},
            })

        with self.assertRaisesRegex(SettingsPreviewError, "must be adb"):
            self.service.preview(document_to_protocol(default_settings()), {
                "source": "window",
                "target": {"kind": "region", "id": "slot"},
            })

        missing = SettingsPreviewService(adb_backend_factory=lambda _settings: MissingAdbBackend())
        with self.assertRaisesRegex(SettingsPreviewError, "Could not capture"):
            missing.preview(document_to_protocol(default_settings()), {
                "source": "adb",
                "target": {"kind": "region", "id": "slot"},
            })


if __name__ == "__main__":
    unittest.main()
