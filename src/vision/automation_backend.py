import io
import os
import subprocess

from PIL import Image


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class AdbCommandError(RuntimeError):
    pass


class AutomationBackendCancelled(RuntimeError):
    pass


class AdbAutomationBackend:
    """Capture and tap an Android emulator through ADB without taking mouse focus."""

    name = "ADB"

    def __init__(self, settings):
        self.settings = settings
        adb_settings = settings.get("adb", {})
        self.adb_path = adb_settings.get("adb_path", "adb") or "adb"
        self.device_serial = (adb_settings.get("device_serial") or "").strip()
        self.coordinate_width = int(adb_settings.get("coordinate_width", 1280) or 1280)
        self.coordinate_height = int(adb_settings.get("coordinate_height", 720) or 720)
        self.timeout_seconds = float(adb_settings.get("command_timeout_seconds", 10.0) or 10.0)
        self._screen_size = None
        self._last_screenshot = None
        self.cancel_check = lambda: False

    def _checkpoint(self):
        if self.cancel_check():
            raise AutomationBackendCancelled("Automation was cancelled.")

    def capture_region(self, region):
        self._checkpoint()
        screenshot = self._capture_screen()
        self._checkpoint()
        left, top, right, bottom = self._scale_region(region, screenshot.size)
        return screenshot.crop((left, top, right, bottom))

    def capture_regions(self, regions):
        self._checkpoint()
        screenshot = self._capture_screen()
        self._checkpoint()
        return {
            name: screenshot.crop(self._scale_region(region, screenshot.size))
            for name, region in regions.items()
        }

    def click_point(self, point):
        self._checkpoint()
        width, height = self._get_screen_size()
        self._checkpoint()
        x, y = self._scale_point(point, width, height)
        self._run_adb(["shell", "input", "tap", str(x), str(y)])

    def _capture_screen(self):
        self._checkpoint()
        data = self._run_adb(["exec-out", "screencap", "-p"], binary=True)
        self._checkpoint()
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as exc:
            raise AdbCommandError(f"ADB returned an unreadable screenshot: {exc}") from exc

        self._screen_size = image.size
        self._last_screenshot = image
        return image

    def _get_screen_size(self):
        if self._screen_size:
            return self._screen_size
        if self._last_screenshot:
            self._screen_size = self._last_screenshot.size
            return self._screen_size
        return self._capture_screen().size

    def _scale_region(self, region, screen_size):
        width, height = screen_size
        left, top = self._scale_xy(region["x"], region["y"], width, height)
        right, bottom = self._scale_xy(
            int(region["x"]) + int(region["width"]),
            int(region["y"]) + int(region["height"]),
            width,
            height,
        )
        left = max(0, min(width, left))
        top = max(0, min(height, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
        return left, top, right, bottom

    def _scale_point(self, point, screen_width, screen_height):
        return self._scale_xy(point["x"], point["y"], screen_width, screen_height)

    def _scale_xy(self, x, y, screen_width, screen_height):
        scaled_x = round(int(x) * screen_width / self.coordinate_width)
        scaled_y = round(int(y) * screen_height / self.coordinate_height)
        return scaled_x, scaled_y

    def _run_adb(self, args, binary=False):
        self._checkpoint()
        command = [self.adb_path]
        if self.device_serial:
            command.extend(["-s", self.device_serial])
        command.extend(args)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise AdbCommandError(
                f"Could not find adb executable '{self.adb_path}'. Configure the ADB path in Settings."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbCommandError(f"ADB command timed out after {self.timeout_seconds:g}s: {' '.join(args)}") from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            details = stderr or stdout or f"exit code {result.returncode}"
            raise AdbCommandError(f"ADB command failed: {' '.join(args)} ({details})")

        self._checkpoint()
        return result.stdout if binary else result.stdout.decode("utf-8", errors="replace")
