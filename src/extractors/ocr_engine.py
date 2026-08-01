import pytesseract
from src.desktop.runtime_paths import resolve_tesseract_path

pytesseract.pytesseract.tesseract_cmd = resolve_tesseract_path() or "tesseract"

def read_text_psm7(pil_image):
    """Reads image assuming a single uniform line of text (Good for Set, Slot, Main Stat)."""
    return pytesseract.image_to_string(pil_image, config="--psm 7").strip()

def read_text_psm6(pil_image):
    """Reads image assuming a uniform block of text (Good for multiple Substats)."""
    return pytesseract.image_to_string(pil_image, config="--psm 6").strip()
