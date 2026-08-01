import cv2
import numpy as np
import re
import pytesseract
import os
from PIL import ImageOps, ImageEnhance, ImageFilter, Image
from src.core.workspace_paths import DEFAULT_DEVELOPMENT_USER_DATA
from src.desktop.runtime_paths import resolve_tesseract_path
from src.extractors.candidates import rank_enhancement_candidates

# Setup Tesseract path for the badge extractor
pytesseract.pytesseract.tesseract_cmd = resolve_tesseract_path() or "tesseract"

# Define the target debug directory
DEBUG_DIR = os.fspath(DEFAULT_DEVELOPMENT_USER_DATA / "debug_images")
MIN_BADGE_MASK_AREA_RATIO = 0.16
MIN_BADGE_MASK_AREA_PIXELS = 180
MIN_BADGE_CONTOUR_AREA_PIXELS = 80
MIN_BADGE_WIDTH_RATIO = 0.45
MIN_BADGE_HEIGHT_RATIO = 0.45


def choose_enhancement_candidates(ocr_texts, limit=3):
    candidates = []
    for text in ocr_texts:
        for candidate in rank_enhancement_candidates(text, limit=limit):
            candidates.append({
                **candidate,
                "matched_alias": candidate.get("matched_alias", text),
                "raw_text": text,
            })

    if not candidates:
        return []

    best_by_value = {}
    for candidate in candidates:
        value = candidate["value"]
        if value not in best_by_value or candidate["score"] > best_by_value[value]["score"]:
            best_by_value[value] = candidate

    ranked = sorted(best_by_value.values(), key=lambda item: item["score"], reverse=True)
    best = ranked[0]
    if len(best["value"].replace("+", "")) == 1:
        two_digit = [
            candidate for candidate in ranked
            if len(candidate["value"].replace("+", "")) == 2 and candidate["score"] >= 0.9
        ]
        if two_digit:
            promoted = two_digit[0]
            ranked = [promoted] + [candidate for candidate in ranked if candidate["value"] != promoted["value"]]

    return ranked[:limit]


def _append_unique_text(texts, value):
    cleaned = str(value or "").strip()
    if cleaned and cleaned not in texts:
        texts.append(cleaned)


def _read_badge_text_variants(pil_image, patch, threshold_patch):
    texts = []
    configs = [
        "--psm 7 -c tessedit_char_whitelist=+0123456789",
        "--psm 8 -c tessedit_char_whitelist=+0123456789",
        "--psm 10 -c tessedit_char_whitelist=+0123456789",
        "--psm 13 -c tessedit_char_whitelist=+0123456789",
    ]

    variants = [threshold_patch]

    gray_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    _, otsu = cv2.threshold(gray_patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    variants.append(otsu)

    hsv_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    bright_text = cv2.inRange(hsv_patch, (0, 0, 120), (179, 190, 255))
    variants.append(bright_text)

    dark_text = cv2.inRange(gray_patch, 0, 110)
    variants.append(dark_text)

    for variant in variants:
        canvas = cv2.copyMakeBorder(variant, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=0)
        scaled = cv2.resize(canvas, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        for config in configs:
            _append_unique_text(texts, pytesseract.image_to_string(scaled, config=config))

    raw_scaled = pil_image.resize((pil_image.width * 8, pil_image.height * 8), Image.Resampling.LANCZOS)
    for config in configs:
        _append_unique_text(texts, pytesseract.image_to_string(raw_scaled, config=config))

    return texts


def _has_badge_like_mask(mask_area, image_width, image_height):
    min_area = max(MIN_BADGE_MASK_AREA_PIXELS, int(image_width * image_height * MIN_BADGE_MASK_AREA_RATIO))
    return mask_area >= min_area


def _is_badge_like_contour(contour_area, bbox, image_width, image_height):
    _, _, width, height = bbox
    return (
        contour_area >= MIN_BADGE_CONTOUR_AREA_PIXELS
        and width >= image_width * MIN_BADGE_WIDTH_RATIO
        and height >= image_height * MIN_BADGE_HEIGHT_RATIO
    )


def preprocess_for_ocr(img, debug_name=None, debug_dir=None):
    """Prepares an image for perfect OCR/AI reading and saves a debug copy."""
    gray = ImageOps.grayscale(img)
    inverted = ImageOps.invert(gray)
    enhanced = ImageEnhance.Contrast(inverted).enhance(4.0)
    sharpened = enhanced.filter(ImageFilter.SHARPEN)
    final_img = sharpened.resize((sharpened.width * 3, sharpened.height * 3), Image.Resampling.LANCZOS)

    if debug_name:
        target_debug_dir = os.fspath(debug_dir) if debug_dir is not None else DEBUG_DIR
        os.makedirs(target_debug_dir, exist_ok=True)
        debug_path = os.path.join(target_debug_dir, f"debug_{debug_name}.png")
        final_img.save(debug_path)

    return final_img


def extract_badge_number_details(pil_image, debug_dir=None):
    """Uses OpenCV to isolate the orange '+' badge and returns OCR evidence."""
    target_debug_dir = os.fspath(debug_dir) if debug_dir is not None else DEBUG_DIR
    os.makedirs(target_debug_dir, exist_ok=True)
    raw_debug_path = os.path.join(target_debug_dir, "debug_enhance_raw.png")
    pil_image.save(raw_debug_path)

    arr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)

    # Filter out everything except the orange/red badge
    red_low = cv2.inRange(hsv, (0, 70, 80), (15, 255, 255))
    red_high = cv2.inRange(hsv, (160, 70, 80), (179, 255, 255))
    orange = cv2.inRange(hsv, (4, 80, 90), (25, 255, 255))
    mask = cv2.bitwise_or(cv2.bitwise_or(red_low, red_high), orange)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    mask_debug_path = os.path.join(target_debug_dir, "debug_badge_mask.png")
    cv2.imwrite(mask_debug_path, mask)

    mask_area = int(cv2.countNonZero(mask))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = {
        "value": "+0",
        "score": 0.0,
        "raw_text": "",
        "present": False,
        "readable": True,
        "candidates": [{"value": "+0", "score": 1.0, "matched_alias": "+0"}],
        "mask_area": mask_area,
    }

    if not _has_badge_like_mask(mask_area, arr.shape[1], arr.shape[0]):
        best["score"] = 1.0
        return best

    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        contour_area = cv2.contourArea(contour)
        if not _is_badge_like_contour(contour_area, (x, y, cw, ch), arr.shape[1], arr.shape[0]):
            continue
        best["present"] = True
        best["readable"] = False
        best["score"] = 0.0
        best["value"] = ""
        best["candidates"] = []

        pad = 2
        patch = arr[
            max(0, y - pad):min(arr.shape[0], y + ch + pad), max(0, x - pad):min(arr.shape[1], x + cw + pad)]
        if patch.size == 0: continue

        gray_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray_patch, 160, 255, cv2.THRESH_BINARY_INV)

        out = cv2.resize(thresh, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
        out = cv2.GaussianBlur(out, (3, 3), 0)

        patch_debug_path = os.path.join(target_debug_dir, "debug_badge_patch.png")
        cv2.imwrite(patch_debug_path, out)

        ocr_texts = _read_badge_text_variants(pil_image, patch, thresh)
        candidates = choose_enhancement_candidates(ocr_texts, limit=3)

        exact_candidates = []
        for text in ocr_texts:
            match = re.search(r'\+?\s*(\d{1,2})', text)
            if not match:
                continue
            val = int(match.group(1))
            if 0 <= val <= 15:
                exact_candidates.append({"value": f"+{val}", "score": 1.0, "matched_alias": text, "raw_text": text})

        for exact_candidate in reversed(exact_candidates):
            has_strong_two_digit = any(
                len(candidate["value"].replace("+", "")) == 2 and candidate["score"] >= 0.9
                for candidate in candidates
            )
            is_one_digit = len(exact_candidate["value"].replace("+", "")) == 1
            if is_one_digit and has_strong_two_digit:
                continue
            candidates = [
                exact_candidate,
                *[candidate for candidate in candidates if candidate["value"] != exact_candidate["value"]],
            ]

        if candidates and candidates[0]["score"] > best["score"]:
            best.update({
                "value": candidates[0]["value"],
                "score": candidates[0]["score"],
                "raw_text": " | ".join(text for text in ocr_texts if text),
                "readable": True,
                "candidates": candidates,
            })

    if not best["present"]:
        best["value"] = "+0"
        best["score"] = 1.0
        best["readable"] = True
        best["candidates"] = [{"value": "+0", "score": 1.0, "matched_alias": "+0"}]

    return best


def extract_badge_number(pil_image):
    """Backward-compatible wrapper returning only the enhancement text."""
    return extract_badge_number_details(pil_image)["value"]
