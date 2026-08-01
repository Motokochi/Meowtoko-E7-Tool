import cv2
import numpy as np
import os


def match_set_icon(captured_pil_image, threshold=0.55):
    """
    Uses Multi-Scale OpenCV Template Matching.
    Uses TM_CCOEFF_NORMED with Alpha Masking.
    This prevents 'White' pixels from dominating the math, forcing it to respect Red vs Blue backgrounds.
    """
    assets_dir = os.path.join("assets", "gear_sets")
    if not os.path.exists(assets_dir):
        return None, 0.0

    # Convert screen capture to BGR
    screen_img = cv2.cvtColor(np.array(captured_pil_image), cv2.COLOR_RGB2BGR)

    best_match = None
    best_score = 0.0

    for icon_filename in os.listdir(assets_dir):
        if not icon_filename.endswith(".png"):
            continue

        set_name = icon_filename.replace(".png", "")
        template_path = os.path.join(assets_dir, icon_filename)

        # Load the template with Alpha channel
        template_full = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template_full is None:
            continue

        has_alpha = template_full.shape[2] == 4
        if has_alpha:
            template_bgr = template_full[:, :, :3]
            template_mask = template_full[:, :, 3]
        else:
            template_bgr = template_full
            template_mask = None

        # Multi-Scale Matching (40% to 120% size)
        for scale in np.linspace(0.4, 1.2, 20):
            width = int(template_bgr.shape[1] * scale)
            height = int(template_bgr.shape[0] * scale)

            if width <= 0 or height <= 0 or height > screen_img.shape[0] or width > screen_img.shape[1]:
                continue

            resized_template = cv2.resize(template_bgr, (width, height), interpolation=cv2.INTER_AREA)

            if has_alpha:
                resized_mask = cv2.resize(template_mask, (width, height), interpolation=cv2.INTER_AREA)
                # TM_CCOEFF_NORMED balances the colors natively, stopping white from overpowering the score
                try:
                    res = cv2.matchTemplate(screen_img, resized_template, cv2.TM_CCOEFF_NORMED, mask=resized_mask)
                except Exception:
                    # Fallback if your specific OpenCV version rejects masks on CCOEFF
                    res = cv2.matchTemplate(screen_img, resized_template, cv2.TM_CCOEFF_NORMED)
            else:
                res = cv2.matchTemplate(screen_img, resized_template, cv2.TM_CCOEFF_NORMED)

            _, max_val, _, _ = cv2.minMaxLoc(res)

            if max_val > best_score:
                best_score = max_val
                best_match = set_name

    # CCOEFF scores are mathematically stricter, so the baseline threshold is lowered to 0.55
    if best_score >= threshold:
        return best_match, best_score

    return None, best_score