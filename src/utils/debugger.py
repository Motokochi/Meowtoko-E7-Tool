"""
Formatting utilities for debugging the AI/OCR pipeline.
"""

import json


def _format_candidates(candidates):
    if not candidates:
        return "[]"
    return json.dumps(candidates, ensure_ascii=True)


def format_debug_basic(title, source, raw_ocr, raw_ai_json, final_stat):
    """
    Backward-compatible debug block for simple extraction logs.
    """
    clean_ocr = raw_ocr.replace('\n', ' \\n ')

    return (
        "\n" + "=" * 60 + "\n" +
        f"{title.upper()} EXTRACTION DEBUG LOG".center(60) + "\n" +
        "=" * 60 + "\n" +
        f"SOURCE         : {source}\n" +
        "-" * 60 + "\n" +
        f"1. RAW OCR     : '{clean_ocr}'\n" +
        f"2. RAW AI JSON : {raw_ai_json}\n" +
        "-" * 60 + "\n" +
        f"FINAL UI VALUE : '{final_stat}'\n" +
        "=" * 60 + "\n"
    )


def format_debug_choice(title, source, raw_ocr, ocr_candidates, raw_ai_json, final_stat):
    """Debug block for finite-vocabulary fields such as slot and set."""
    clean_ocr = raw_ocr.replace('\n', ' \\n ')

    return (
        "\n" + "=" * 60 + "\n" +
        f"{title.upper()} EXTRACTION DEBUG LOG".center(60) + "\n" +
        "=" * 60 + "\n" +
        f"SOURCE         : {source}\n" +
        "-" * 60 + "\n" +
        f"1. RAW OCR     : '{clean_ocr}'\n" +
        f"2. OCR MATCHES : {_format_candidates(ocr_candidates)}\n" +
        f"3. RAW AI JSON : {raw_ai_json}\n" +
        "-" * 60 + "\n" +
        f"FINAL UI VALUE : '{final_stat}'\n" +
        "=" * 60 + "\n"
    )


def format_debug_enhancement(source, ocr_details, raw_ai_json, final_value):
    """Debug block for enhancement badge extraction."""
    return (
        "\n" + "=" * 60 + "\n" +
        "ENHANCEMENT EXTRACTION DEBUG LOG".center(60) + "\n" +
        "=" * 60 + "\n" +
        f"SOURCE         : {source}\n" +
        "-" * 60 + "\n" +
        f"1. BADGE SEEN  : {ocr_details.get('present', False)}\n" +
        f"2. BADGE READ  : {ocr_details.get('readable', False)}\n" +
        f"3. RAW OCR     : '{ocr_details.get('raw_text', '')}'\n" +
        f"4. OCR MATCHES : {_format_candidates(ocr_details.get('candidates'))}\n" +
        f"5. RAW AI JSON : {raw_ai_json}\n" +
        "-" * 60 + "\n" +
        f"FINAL UI VALUE : '{final_value}'\n" +
        "=" * 60 + "\n"
    )


def format_debug_main_stat(slot, raw_ocr, raw_ai_json, final_stat):
    """
    Backward-compatible main stat debug block.
    """
    clean_ocr = raw_ocr.replace('\n', ' \\n ')

    return (
        "\n" + "=" * 60 + "\n" +
        "MAIN STAT EXTRACTION DEBUG LOG".center(60) + "\n" +
        "=" * 60 + "\n" +
        f"CURRENT SLOT   : {slot}\n" +
        "-" * 60 + "\n" +
        f"1. RAW OCR     : '{clean_ocr}'\n" +
        f"2. RAW AI JSON : {raw_ai_json}\n" +
        "-" * 60 + "\n" +
        f"FINAL UI VALUE : '{final_stat}'\n" +
        "=" * 60 + "\n"
    )


def format_debug_main_stat_details(slot, source, raw_ocr, ocr_candidates, raw_ai_json, final_stat):
    """Detailed main stat block including OCR candidates."""
    clean_ocr = raw_ocr.replace('\n', ' \\n ')

    return (
        "\n" + "=" * 60 + "\n" +
        "MAIN STAT EXTRACTION DEBUG LOG".center(60) + "\n" +
        "=" * 60 + "\n" +
        f"CURRENT SLOT   : {slot}\n" +
        f"SOURCE         : {source}\n" +
        "-" * 60 + "\n" +
        f"1. RAW OCR     : '{clean_ocr}'\n" +
        f"2. OCR MATCHES : {_format_candidates(ocr_candidates)}\n" +
        f"3. RAW AI JSON : {raw_ai_json}\n" +
        "-" * 60 + "\n" +
        f"FINAL UI VALUE : '{final_stat}'\n" +
        "=" * 60 + "\n"
    )


def format_debug_subs(raw_ocr, raw_ai_json, final_subs, ocr_candidates=None):
    """
    Returns a visible formatted block to compare OCR evidence vs AI JSON.
    """
    clean_ocr = raw_ocr.replace('\n', ' \\n ')
    subs_list_str = "\n".join([f"  - {s['stat']:<10} : {s['val']}" for s in final_subs])

    return (
        "\n" + "=" * 60 + "\n" +
        "SUBSTATS EXTRACTION DEBUG LOG".center(60) + "\n" +
        "=" * 60 + "\n" +
        f"1. RAW OCR     : '{clean_ocr}'\n" +
        f"2. OCR MATCHES : {_format_candidates(ocr_candidates)}\n" +
        f"3. RAW AI JSON : {raw_ai_json}\n" +
        "-" * 60 + "\n" +
        f"FINAL UI VALUES:\n{subs_list_str}\n" +
        "=" * 60 + "\n"
    )
