"""
OCR candidate ranking helpers.

The scanner treats OCR as a signal, not a verdict: every raw OCR string is
matched against the finite Epic Seven vocabulary and carried forward with a
confidence score for AI verification and final validation.
"""

import json
import re

from src.constants import ALL_STATS
from src.optimizer.domain import (
    gear_slot_match_aliases_by_display,
    item_stat_match_aliases_by_display,
    set_match_aliases_by_display,
)
GEAR_CONTEXT_WORDS = {
    "ancient", "epic", "good", "heroic", "legendary", "normal", "otherworldly",
    "rare", "reforged", "equipment", "gear",
}

SET_ALIASES = set_match_aliases_by_display()
SLOT_ALIASES = gear_slot_match_aliases_by_display()
STAT_ALIASES = item_stat_match_aliases_by_display()


def _levenshtein_similarity(left, right):
    left, right = left.upper(), right.upper()
    if len(left) < len(right):
        left, right = right, left
    if not left:
        return 1.0
    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, 1):
        current = [row]
        for column, right_character in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_character != right_character),
            ))
        previous = current
    return round(1 - previous[-1] / len(left), 4)


def clean_for_match(text):
    """Normalizes OCR/model text while keeping symbols that change meaning."""
    return re.sub(r"[^a-z0-9%+]", "", str(text).lower())


def _option_aliases(option, aliases=None):
    values = [option]
    if aliases and option in aliases:
        values.extend(aliases[option])
    return values


def _text_variants(raw_text, ignored_words=None, max_span=4):
    words = re.findall(r"[a-z0-9%+]+", str(raw_text).lower())
    ignored = ignored_words or set()
    filtered_words = [word for word in words if word not in ignored]

    variants = {clean_for_match(raw_text)}
    for word_list in (words, filtered_words):
        if not word_list:
            continue
        variants.add(clean_for_match(" ".join(word_list)))
        for start in range(len(word_list)):
            for end in range(start + 1, min(len(word_list), start + max_span) + 1):
                variants.add(clean_for_match(" ".join(word_list[start:end])))

    variants.discard("")
    return variants


def rank_options(raw_text, options, aliases=None, limit=3, ignored_words=None):
    """
    Ranks finite options by Levenshtein similarity against OCR/model text.

    Long OCR snippets often include nearby gear metadata. To avoid scoring
    "helmet" against "otherworldly epic helme", we compare options against the
    full string and against local word spans.
    """
    text_variants = _text_variants(raw_text, ignored_words=ignored_words)
    if not text_variants:
        return []

    ranked = []
    for option in options:
        best_score = 0.0
        best_alias = option
        for alias in _option_aliases(option, aliases):
            alias_cleaned = clean_for_match(alias)
            for text in text_variants:
                score = _levenshtein_similarity(alias_cleaned, text)
                if score > best_score:
                    best_score = score
                    best_alias = alias
        ranked.append({
            "value": option,
            "score": round(best_score, 4),
            "matched_alias": best_alias,
        })

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]


def best_candidate(raw_text, options, aliases=None, threshold=0.0, ignored_words=None):
    """Returns the best ranked option when it clears the threshold."""
    ranked = rank_options(raw_text, options, aliases=aliases, limit=1, ignored_words=ignored_words)
    if not ranked or ranked[0]["score"] < threshold:
        return None
    return ranked[0]


def rank_stat_candidates(raw_text, allowed_stats=None, ai_value_reference="", limit=3):
    """Ranks Epic Seven stat names, including flat-vs-percent disambiguation."""
    allowed = allowed_stats or ALL_STATS
    combined = f"{raw_text} {ai_value_reference}".lower()
    raw_has_percent = "%" in combined or "percent" in combined or "percentage" in combined
    raw_has_flat = "flat" in combined

    ranked = rank_options(raw_text, allowed, aliases=STAT_ALIASES, limit=len(allowed))
    adjusted = []
    for item in ranked:
        stat = item["value"]
        score = item["score"]
        if stat in {"Attack", "Health", "Defense"}:
            if raw_has_percent:
                score += 0.12
            elif not raw_has_flat:
                score -= 0.2
        elif stat in {"Flat Attack", "Flat Health", "Flat Defense"}:
            if raw_has_flat:
                score += 0.12
            elif raw_has_percent:
                score -= 0.25
            else:
                score += 0.12

        adjusted.append({
            **item,
            "score": round(max(0.0, min(1.0, score)), 4),
        })

    adjusted.sort(key=lambda item: item["score"], reverse=True)
    return adjusted[:limit]


def rank_enhancement_candidates(raw_text, limit=3):
    """Ranks enhancement levels from +0 through +15."""
    cleaned = re.sub(r"[^+\d]", "", str(raw_text))
    if not cleaned:
        return []

    if cleaned.isdigit():
        cleaned = f"+{cleaned}"

    digits = re.sub(r"\D", "", cleaned)
    strong_candidates = []
    if len(digits) >= 2:
        first_two = digits[:2]
        if 0 <= int(first_two) <= 15:
            strong_candidates.append({
                "value": f"+{int(first_two)}",
                "score": 1.0,
                "matched_alias": cleaned,
            })

        # Tesseract often turns a leading 1 into 7 on orange enhancement badges
        # (+13 -> +73). Preserve the second digit rather than collapsing to +3.
        if digits[0] == "7":
            corrected = f"1{digits[1]}"
            if 10 <= int(corrected) <= 15:
                strong_candidates.append({
                    "value": f"+{int(corrected)}",
                    "score": 0.95,
                    "matched_alias": cleaned,
                })

    levels = [f"+{i}" for i in range(16)]
    ranked = rank_options(cleaned, levels, limit=len(levels))
    for candidate in reversed(strong_candidates):
        ranked = [item for item in ranked if item["value"] != candidate["value"]]
        ranked.insert(0, candidate)
    return ranked[:limit]


def candidates_for_prompt(candidates):
    """Compact, stable JSON for prompt context."""
    return json.dumps(candidates or [], ensure_ascii=True)
