"""Safe, typed previews for unsaved desktop capture settings."""

from __future__ import annotations

import base64
import io
from collections.abc import Callable, Mapping
from typing import Any

from PIL import ImageDraw

from src.core.settings_service import (
    LEVEL_NAMES,
    POINT_PROTOCOL_TO_FILE,
    REGION_PROTOCOL_TO_FILE,
    protocol_settings_to_document,
)

PREVIEW_SOURCES = frozenset({"adb"})
PREVIEW_SIZE = 110
MAX_PREVIEW_DIMENSION = 4096
MAX_PREVIEW_PIXELS = 16_777_216


class SettingsPreviewError(RuntimeError):
    pass


AdbBackendFactory = Callable[[Mapping[str, Any]], Any]


def _create_adb_backend(settings: Mapping[str, Any]):
    from src.vision.automation_backend import AdbAutomationBackend

    return AdbAutomationBackend(settings)


class SettingsPreviewService:
    def __init__(
        self,
        *,
        adb_backend_factory: AdbBackendFactory | None = None,
    ):
        self.adb_backend_factory = adb_backend_factory or _create_adb_backend

    def preview(
        self,
        raw_settings: Mapping[str, Any],
        raw_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        document = protocol_settings_to_document(raw_settings)
        source, kind, item_id = self._validate_request(raw_request)
        region, label, point = self._resolve_region(document, kind, item_id)
        self._validate_region_size(region)

        try:
            image = self.adb_backend_factory(document).capture_region(region)
        except Exception as error:
            raise SettingsPreviewError(f"ADB preview failed: {error}") from error

        if image is None:
            raise SettingsPreviewError("Could not capture the configured ADB device.")
        if not hasattr(image, "size") or not hasattr(image, "save"):
            raise SettingsPreviewError("Capture provider returned an invalid image.")

        rendered = image.convert("RGB")
        if point is not None:
            self._draw_crosshair(rendered, region, point)
        width, height = rendered.size
        if width < 1 or height < 1:
            raise SettingsPreviewError("Capture provider returned an empty image.")

        encoded = io.BytesIO()
        rendered.save(encoded, format="PNG", optimize=True)
        data_url = "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
        return {
            "source": source,
            "kind": kind,
            "itemId": item_id,
            "label": label,
            "width": width,
            "height": height,
            "dataUrl": data_url,
        }

    @staticmethod
    def _validate_request(raw_request: Mapping[str, Any]) -> tuple[str, str, str]:
        if set(raw_request) != {"source", "target"}:
            raise SettingsPreviewError("Preview request requires only source and target.")
        source = raw_request.get("source")
        target = raw_request.get("target")
        if source not in PREVIEW_SOURCES:
            raise SettingsPreviewError("Preview source must be adb.")
        if not isinstance(target, Mapping) or set(target) != {"kind", "id"}:
            raise SettingsPreviewError("Preview target requires only kind and id.")
        kind = target.get("kind")
        item_id = target.get("id")
        if kind not in {"region", "point", "level"} or not isinstance(item_id, str):
            raise SettingsPreviewError("Preview target is invalid.")
        if kind == "region" and item_id not in REGION_PROTOCOL_TO_FILE:
            raise SettingsPreviewError("Preview region is unsupported.")
        if kind == "point" and item_id not in POINT_PROTOCOL_TO_FILE:
            raise SettingsPreviewError("Preview click point is unsupported.")
        if kind == "level" and item_id not in LEVEL_NAMES:
            raise SettingsPreviewError("Preview enhancement level is unsupported.")
        return source, kind, item_id

    @staticmethod
    def _resolve_region(
        document: Mapping[str, Any],
        kind: str,
        item_id: str,
    ) -> tuple[dict[str, int], str, Mapping[str, int] | None]:
        if kind == "region":
            file_id = REGION_PROTOCOL_TO_FILE[item_id]
            return dict(document["regions"][file_id]), f"Capture region: {item_id}", None

        if kind == "level":
            point = document["click_points"]["levels"][item_id]
            label = f"Enhancement level {item_id}"
        else:
            file_id = POINT_PROTOCOL_TO_FILE[item_id]
            point = document["click_points"][file_id]
            label = f"Click point: {item_id}"
        region = {
            "x": max(0, int(point["x"]) - PREVIEW_SIZE // 2),
            "y": max(0, int(point["y"]) - PREVIEW_SIZE // 2),
            "width": PREVIEW_SIZE,
            "height": PREVIEW_SIZE,
        }
        return region, label, point

    @staticmethod
    def _validate_region_size(region: Mapping[str, int]) -> None:
        width = int(region["width"])
        height = int(region["height"])
        if (
            width > MAX_PREVIEW_DIMENSION
            or height > MAX_PREVIEW_DIMENSION
            or width * height > MAX_PREVIEW_PIXELS
        ):
            raise SettingsPreviewError("Preview is limited to 4096 pixels per side and 16 megapixels.")

    @staticmethod
    def _draw_crosshair(image, region: Mapping[str, int], point: Mapping[str, int]) -> None:
        width, height = image.size
        relative_x = round((int(point["x"]) - int(region["x"])) * width / int(region["width"]))
        relative_y = round((int(point["y"]) - int(region["y"])) * height / int(region["height"]))
        relative_x = max(0, min(width - 1, relative_x))
        relative_y = max(0, min(height - 1, relative_y))
        draw = ImageDraw.Draw(image)
        draw.line((relative_x, 0, relative_x, height - 1), fill="red", width=2)
        draw.line((0, relative_y, width - 1, relative_y), fill="red", width=2)
        radius = min(5, relative_x, relative_y, width - 1 - relative_x, height - 1 - relative_y)
        if radius > 0:
            draw.ellipse(
                (relative_x - radius, relative_y - radius, relative_x + radius, relative_y + radius),
                outline="yellow",
                width=2,
            )
