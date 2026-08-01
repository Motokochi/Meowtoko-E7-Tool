import re
from src.constants import STAT_TRANSLATION, ALL_SETS, ALL_SLOTS, SLOT_MAIN_STATS
from src.extractors.candidates import (
    SET_ALIASES,
    SLOT_ALIASES,
    GEAR_CONTEXT_WORDS,
    best_candidate,
    rank_options,
    rank_stat_candidates,
)


def fuzzy_match_set(raw_text):
    """Cleans OCR text and uses Levenshtein to perfectly match an Epic Seven Set."""
    candidate = best_candidate(raw_text, ALL_SETS, aliases=SET_ALIASES, threshold=0.5)
    return candidate["value"] if candidate else None


def fuzzy_match_set_details(raw_text, limit=3):
    """Returns ranked set candidates with scores for debug logs and AI prompts."""
    return rank_options(raw_text, ALL_SETS, aliases=SET_ALIASES, limit=limit)


def fuzzy_match_slot(raw_text):
    """Cleans OCR text and uses Levenshtein to perfectly match an Epic Seven Slot."""
    candidate = best_candidate(raw_text, ALL_SLOTS, aliases=SLOT_ALIASES, threshold=0.5, ignored_words=GEAR_CONTEXT_WORDS)
    return candidate["value"] if candidate else None


def fuzzy_match_slot_details(raw_text, limit=3):
    """Returns ranked slot candidates with scores for debug logs and AI prompts."""
    return rank_options(raw_text, ALL_SLOTS, aliases=SLOT_ALIASES, limit=limit, ignored_words=GEAR_CONTEXT_WORDS)


def resolve_main_stat(raw_text, slot, ai_value_reference=""):
    """
    Extremely strict Main Stat resolver.
    1. Auto-resolves left-side gear without even looking at OCR.
    2. Uses Levenshtein + Translation for right-side gear.
    3. Enforces that the result actually exists in SLOT_MAIN_STATS.
    """
    # 1. Left-Side Gear is guaranteed! Bypass OCR/AI completely.
    if slot == "Weapon": return "Flat Attack"
    if slot == "Helmet": return "Flat Health"
    if slot == "Armor": return "Flat Defense"

    # 2. Right-Side Gear (Necklace, Ring, Boots)
    valid_mains = SLOT_MAIN_STATS.get(slot, [])
    candidates = rank_stat_candidates(raw_text, valid_mains, ai_value_reference, limit=1)
    if candidates and candidates[0]["score"] > 0.4:
        return candidates[0]["value"]

    return None


def resolve_stat_name(raw_text, ai_value_reference=""):
    """Used for Substats. Standardizes AI/OCR text into clean UI variables."""
    candidates = rank_stat_candidates(raw_text, ai_value_reference=ai_value_reference, limit=1)
    if candidates and candidates[0]["score"] > 0.35:
        return candidates[0]["value"]

    # Keep the old translation table as a conservative fallback for odd OCR
    # strings that still contain an exact long-form stat name.
    cleaned_text = re.sub(r'[^a-zA-Z\s]', '', raw_text).strip().lower()
    for key, base_stat in STAT_TRANSLATION.items():
        if key in cleaned_text:
            if base_stat in ["Attack", "Health", "Defense"]:
                has_percent = '%' in raw_text or '%' in str(ai_value_reference)
                return base_stat if has_percent else f"Flat {base_stat}"
            return base_stat

    return None
