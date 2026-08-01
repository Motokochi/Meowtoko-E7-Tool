import sys
import types
import unittest

sys.modules.setdefault("cv2", types.SimpleNamespace())
sys.modules.setdefault(
    "pytesseract",
    types.SimpleNamespace(
        pytesseract=types.SimpleNamespace(tesseract_cmd=""),
        image_to_string=lambda *args, **kwargs: "",
    ),
)

from src.vision.filters import _has_badge_like_mask, _is_badge_like_contour


class BadgeDetectionTests(unittest.TestCase):
    def test_gear_icon_corner_is_not_badge_like(self):
        self.assertFalse(_has_badge_like_mask(mask_area=95, image_width=35, image_height=30))
        self.assertFalse(
            _is_badge_like_contour(
                contour_area=60,
                bbox=(0, 0, 12, 10),
                image_width=35,
                image_height=30,
            )
        )

    def test_real_badge_crop_is_badge_like(self):
        self.assertTrue(_has_badge_like_mask(mask_area=635, image_width=35, image_height=30))
        self.assertTrue(
            _is_badge_like_contour(
                contour_area=420,
                bbox=(0, 0, 31, 24),
                image_width=35,
                image_height=30,
            )
        )


if __name__ == "__main__":
    unittest.main()
