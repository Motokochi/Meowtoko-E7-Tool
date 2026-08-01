import unittest
import threading
from unittest.mock import Mock, patch

from PIL import Image

from src.vision.automation_backend import (
    CREATE_NO_WINDOW,
    AdbAutomationBackend,
    AutomationBackendCancelled,
)


class AdbAutomationBackendTests(unittest.TestCase):
    def test_click_point_scales_from_configured_coordinate_space(self):
        backend = AdbAutomationBackend({
            "adb": {
                "adb_path": "adb",
                "coordinate_width": 1280,
                "coordinate_height": 720,
            }
        })
        backend._screen_size = (2560, 1440)
        backend._run_adb = Mock()

        backend.click_point({"x": 640, "y": 360})

        backend._run_adb.assert_called_once_with(["shell", "input", "tap", "1280", "720"])

    def test_capture_region_returns_scaled_crop(self):
        backend = AdbAutomationBackend({
            "adb": {
                "adb_path": "adb",
                "coordinate_width": 1280,
                "coordinate_height": 720,
            }
        })
        backend._capture_screen = Mock(return_value=Image.new("RGB", (2560, 1440), "black"))

        cropped = backend.capture_region({"x": 100, "y": 50, "width": 300, "height": 100})

        self.assertEqual(cropped.size, (600, 200))

    def test_capture_regions_reuses_single_screenshot(self):
        backend = AdbAutomationBackend({
            "adb": {
                "adb_path": "adb",
                "coordinate_width": 1280,
                "coordinate_height": 720,
            }
        })
        backend._capture_screen = Mock(return_value=Image.new("RGB", (1280, 720), "black"))

        crops = backend.capture_regions({
            "enhance": {"x": 100, "y": 50, "width": 35, "height": 30},
            "subs": {"x": 40, "y": 300, "width": 330, "height": 100},
        })

        backend._capture_screen.assert_called_once()
        self.assertEqual(crops["enhance"].size, (35, 30))
        self.assertEqual(crops["subs"].size, (330, 100))

    def test_cancel_after_screen_size_discovery_prevents_tap(self):
        cancelled = threading.Event()
        backend = AdbAutomationBackend({"adb": {}})
        backend.cancel_check = cancelled.is_set
        backend._get_screen_size = Mock(side_effect=lambda: (cancelled.set() or (1280, 720)))
        backend._run_adb = Mock()

        with self.assertRaises(AutomationBackendCancelled):
            backend.click_point({"x": 100, "y": 100})

        backend._run_adb.assert_not_called()

    @patch("src.vision.automation_backend.subprocess.run")
    def test_adb_commands_do_not_open_a_console_window(self, run):
        run.return_value = Mock(returncode=0, stdout=b"ok", stderr=b"")
        backend = AdbAutomationBackend({"adb": {"adb_path": "adb.exe"}})

        self.assertEqual(backend._run_adb(["devices"]), "ok")
        run.assert_called_once_with(
            ["adb.exe", "devices"],
            capture_output=True,
            check=False,
            timeout=10.0,
            creationflags=CREATE_NO_WINDOW,
        )


if __name__ == "__main__":
    unittest.main()
