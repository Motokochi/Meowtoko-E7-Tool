import unittest

from src.core.config_manager import _merge_defaults, get_default_settings


class ConfigManagerTests(unittest.TestCase):
    def test_merge_defaults_adds_nested_click_points(self):
        loaded = {
            "target_window": "Epic Seven",
            "click_points": {
                "destroy": {"x": 1, "y": 2},
                "levels": {"+3": {"x": 3, "y": 4}},
            },
            "automation": {},
        }
        defaults = {
            "target_window": "Epic Seven",
            "click_points": {
                "destroy": {"x": 10, "y": 20},
                "destroy_confirm": {"x": 30, "y": 40},
                "levels": {
                    "+3": {"x": 50, "y": 60},
                    "+6": {"x": 70, "y": 80},
                },
            },
            "automation": {
                "after_destroy_seconds": 0.6,
                "after_reward_popup_seconds": 0.6,
            },
        }

        merged = _merge_defaults(loaded, defaults)

        self.assertEqual(merged["click_points"]["destroy"], {"x": 1, "y": 2})
        self.assertEqual(merged["click_points"]["destroy_confirm"], {"x": 30, "y": 40})
        self.assertEqual(merged["click_points"]["levels"]["+3"], {"x": 3, "y": 4})
        self.assertEqual(merged["click_points"]["levels"]["+6"], {"x": 70, "y": 80})
        self.assertEqual(merged["automation"]["after_destroy_seconds"], 0.6)
        self.assertEqual(merged["automation"]["after_reward_popup_seconds"], 0.6)

    def test_defaults_use_calibrated_enhancement_coordinates(self):
        defaults = get_default_settings()

        self.assertEqual(defaults["regions"]["enhance"], {"x": 105, "y": 110, "width": 35, "height": 30})
        self.assertEqual(defaults["click_points"]["next_piece"], {"x": 200, "y": 220})
        self.assertEqual(defaults["click_points"]["open_enhance"], {"x": 1150, "y": 700})
        self.assertEqual(defaults["click_points"]["enhance"], {"x": 695, "y": 680})


if __name__ == "__main__":
    unittest.main()
