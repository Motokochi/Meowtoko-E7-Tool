import json
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from PIL import Image

from src.constants import ALL_SETS, ALL_SLOTS, ALL_STATS, SLOT_MAIN_STATS
from src.core.workspace_paths import DEFAULT_DEVELOPMENT_USER_DATA
from src.extractors.candidates import (
    SET_ALIASES,
    SLOT_ALIASES,
    GEAR_CONTEXT_WORDS,
    candidates_for_prompt,
    rank_options,
    rank_stat_candidates,
)
from src.extractors.hybrid_parser import fuzzy_match_set_details, fuzzy_match_slot_details
from src.extractors.llm_client import ensure_ollama_running, query_ollama_vision
from src.extractors.ocr_engine import read_text_psm6, read_text_psm7
from src.utils.debugger import (
    format_debug_choice,
    format_debug_enhancement,
    format_debug_main_stat_details,
    format_debug_subs,
)
from src.vision.filters import extract_badge_number_details, preprocess_for_ocr


class ScanCancelledError(RuntimeError):
    """Raised when a cooperative analyzer scan cancellation is observed."""


class ScanOrchestrator:
    """Manages the OCR + AI verification workflow in a background thread."""

    @staticmethod
    def _prepare_enhancement_ai_image(image):
        scale = 8
        return image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)

    @staticmethod
    def _checkpoint(cancel_check: Callable[[], bool], deadline: float) -> None:
        if cancel_check():
            raise ScanCancelledError("Scan was cancelled.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out while scanning gear.")

    @staticmethod
    def scan(
        images_dict,
        *,
        cancel_check: Callable[[], bool] | None = None,
        on_progress: Callable[[str, str, float], None] | None = None,
        debug_dir: str | os.PathLike[str] | None = None,
        timeout_seconds: float = 90,
    ):
        """Run one scan synchronously with cooperative cancellation and progress."""
        cancelled = cancel_check or (lambda: False)
        progress = on_progress or (lambda _stage, _message, _value: None)
        deadline = time.monotonic() + max(0.01, float(timeout_seconds))
        ScanOrchestrator._checkpoint(cancelled, deadline)
        progress("dependencies", "Checking Ollama vision support…", 0.05)
        if not ensure_ollama_running():
            raise RuntimeError("Could not connect to Ollama. Is it running?")

        target_debug_dir = Path(
            debug_dir or DEFAULT_DEVELOPMENT_USER_DATA / "debug_images"
        )
        target_debug_dir.mkdir(parents=True, exist_ok=True)
        for region_name, img in images_dict.items():
            ScanOrchestrator._checkpoint(cancelled, deadline)
            if img:
                img.save(target_debug_dir / f"raw_crop_{region_name}.png")

        progress("preprocessing", "Preparing captured regions for OCR…", 0.18)
        ScanOrchestrator._checkpoint(cancelled, deadline)
        enhance_ocr = extract_badge_number_details(images_dict["enhance"], debug_dir=target_debug_dir)
        enhance_ai_image = ScanOrchestrator._prepare_enhancement_ai_image(images_dict["enhance"])
        enhance_ai_image.save(target_debug_dir / "debug_enhance_ai.png")
        prep_slot = preprocess_for_ocr(images_dict["slot"], debug_name="slot", debug_dir=target_debug_dir)
        prep_main = preprocess_for_ocr(images_dict["main_stat"], debug_name="main_stat", debug_dir=target_debug_dir)
        prep_subs = preprocess_for_ocr(images_dict["subs"], debug_name="subs", debug_dir=target_debug_dir)
        prep_set = preprocess_for_ocr(images_dict["set"], debug_name="set", debug_dir=target_debug_dir)

        progress("ocr", "Reading gear text with Tesseract…", 0.34)
        ScanOrchestrator._checkpoint(cancelled, deadline)
        raw_slot_text = read_text_psm7(prep_slot)
        raw_main_text = read_text_psm7(prep_main)
        raw_set_text = read_text_psm7(prep_set)
        raw_tesseract_subs = read_text_psm6(prep_subs)

        slot_candidates = fuzzy_match_slot_details(raw_slot_text)
        set_candidates = fuzzy_match_set_details(raw_set_text)
        probable_slot = slot_candidates[0]["value"] if slot_candidates else ""
        valid_main_stats = SLOT_MAIN_STATS.get(probable_slot, ALL_STATS)
        main_candidates = rank_stat_candidates(raw_main_text, valid_main_stats)
        sub_line_candidates = ScanOrchestrator._build_sub_ocr_candidates(raw_tesseract_subs)

        results = {
            "enhance_ocr": enhance_ocr,
            "raw_main": raw_main_text,
            "raw_subs": raw_tesseract_subs,
            "raw_set_ocr": raw_set_text,
            "raw_slot_ocr": raw_slot_text,
            "slot_ocr_candidates": slot_candidates,
            "set_ocr_candidates": set_candidates,
            "main_ocr_candidates": main_candidates,
            "sub_ocr_candidates": sub_line_candidates,
        }

        progress("verification", "Verifying OCR candidates with Ollama…", 0.48)
        tasks = {
            "enhance": (enhance_ai_image, f"""
                        Read the enhancement level on this enlarged Epic Seven gear crop.
                        OCR evidence from the treated image:
                        {json.dumps(enhance_ocr, ensure_ascii=True)}
                        If there is no orange enhancement badge, the answer is "+0".
                        If an orange badge is visible, inspect the image and read the badge text.
                        Do not answer "+0" just because OCR evidence is empty or unreadable.
                        The only valid values are "+0" through "+15".
                        Output ONLY valid JSON with this exact schema:
                        {{"enhance": "+0"}}
                    """),
            "slot": (images_dict["slot"], f"""
                        Identify the equipment slot from this exact list:
                        {', '.join(ALL_SLOTS)}
                        OCR candidates from the treated image, with Levenshtein scores:
                        {candidates_for_prompt(slot_candidates)}
                        Choose exactly one list value. Output ONLY valid JSON:
                        {{"slot": "Weapon"}}
                    """),
            "set": (images_dict["set"], f"""
                        Identify the equipment set from this exact list:
                        {', '.join(ALL_SETS)}
                        OCR candidates from the treated image, with Levenshtein scores:
                        {candidates_for_prompt(set_candidates)}
                        Ignore set count text such as (2/4) or (2/2).
                        Choose exactly one list value. Output ONLY valid JSON:
                        {{"set": "Speed Set"}}
                    """),
            "main_stat": (images_dict["main_stat"], f"""
                        Identify the main stat name and numerical value.
                        Probable slot from OCR: {probable_slot or "unknown"}
                        Valid stat names for that slot:
                        {', '.join(valid_main_stats)}
                        Raw OCR text: {raw_main_text!r}
                        OCR stat candidates with Levenshtein scores:
                        {candidates_for_prompt(main_candidates)}
                        For Attack, Defense, or Health, use a percent stat only when a % sign is visible.
                        Output ONLY valid JSON with this exact schema:
                        {{"name": "Attack", "value": "12%"}}
                    """),
            "subs": (images_dict["subs"], f"""
                        Extract exactly four substats from this Epic Seven gear crop.
                        Raw OCR text from the treated image:
                        {raw_tesseract_subs}
                        OCR line candidates with Levenshtein scores:
                        {json.dumps(sub_line_candidates, ensure_ascii=True)}
                        Ignore roll increases in parentheses such as (+4).
                        Use only these stat names:
                        {', '.join(ALL_STATS)}
                        Output ONLY valid JSON with exactly this schema:
                        {{"substats": [{{"name": "Speed", "value": "4"}}, {{"name": "Critical Hit Chance", "value": "5%"}}]}}
                    """),
        }

        executor = ThreadPoolExecutor(max_workers=min(4, len(tasks)))
        future_to_task = {
            executor.submit(query_ollama_vision, img, prompt): key
            for key, (img, prompt) in tasks.items()
        }
        pending = set(future_to_task)
        completed = 0
        try:
            while pending:
                ScanOrchestrator._checkpoint(cancelled, deadline)
                ready, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in ready:
                    results[future_to_task[future]] = future.result()
                    completed += 1
                    progress(
                        "verification",
                        f"Verified {completed} of {len(tasks)} gear regions…",
                        0.48 + (completed / len(tasks)) * 0.38,
                    )
        except Exception:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

        ScanOrchestrator._checkpoint(cancelled, deadline)
        llm_error = ScanOrchestrator._fatal_llm_error(results)
        if llm_error:
            raise RuntimeError(llm_error)

        progress("parsing", "Resolving verified gear values…", 0.92)
        final_parsed_data, debug_log = ScanOrchestrator._parse_results(results)
        ScanOrchestrator._checkpoint(cancelled, deadline)
        progress("complete", "Gear scan complete.", 1.0)
        return final_parsed_data, debug_log

    @staticmethod
    def run_scan(images_dict, on_complete_callback, on_error_callback):
        """Backward-compatible callback wrapper for asynchronous UI integrations."""
        def worker():
            try:
                parsed_data, debug_log = ScanOrchestrator.scan(images_dict)
                on_complete_callback(parsed_data, debug_log)
            except Exception as error:
                on_error_callback(f"Critical Scan Error: {str(error)}")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def run_scan_blocking(images_dict, timeout_seconds=90):
        """Runs the existing scan flow and blocks until parsed data is available."""
        done = threading.Event()
        result = {}

        def on_complete(parsed_data, debug_log):
            result["parsed_data"] = parsed_data
            result["debug_log"] = debug_log
            done.set()

        def on_error(error_msg):
            result["error"] = error_msg
            done.set()

        ScanOrchestrator.run_scan(images_dict, on_complete, on_error)
        if not done.wait(timeout_seconds):
            raise TimeoutError("Timed out while scanning gear.")
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["parsed_data"], result["debug_log"]

    @staticmethod
    def _build_sub_ocr_candidates(raw_subs_text):
        line_data = []
        for line in raw_subs_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            line_data.append({
                "raw": stripped,
                "value": ScanOrchestrator._extract_numeric_value(stripped),
                "candidates": rank_stat_candidates(stripped, ALL_STATS),
            })
        return line_data

    @staticmethod
    def _safe_json(raw_json):
        try:
            data = json.loads(raw_json)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _fatal_llm_error(results):
        for key in ["enhance", "slot", "set", "main_stat", "subs"]:
            error = str(ScanOrchestrator._safe_json(results.get(key, "{}")).get("error", ""))
            if (
                "unknown model architecture" in error
                or "unsupported architecture" in error
                or "error loading model" in error
            ):
                return (
                    "Ollama cannot load qwen3-vl:8b-instruct with the running Ollama server. "
                    "Update Ollama, restart it, then run `ollama pull qwen3-vl:8b-instruct`."
                )
        return ""

    @staticmethod
    def _best(candidates):
        return candidates[0] if candidates else None

    @staticmethod
    def _extract_numeric_value(text):
        text = re.sub(r"\([^)]*\)", "", str(text))
        text = re.sub(r"(?i)(?<![a-z])b(?=\s*%?\b)", "8", text)
        text = re.sub(r"(?i)(?<=\d)b(?=\s*%?\b)", "8", text)
        match = re.search(r"\d+", text)
        return match.group(0) if match else ""

    @staticmethod
    def _parse_enhancement_value(value):
        match = re.search(r"\+?\s*(\d{1,2})", str(value))
        if not match:
            return None
        level = int(match.group(1))
        if 0 <= level <= 15:
            return f"+{level}"
        return None

    @staticmethod
    def _resolve_enhancement(results):
        evidence = ScanOrchestrator._enhancement_evidence(results)
        ocr = evidence["ocr"]
        ai_value = evidence["ai_value"]
        ocr_value = evidence["ocr_value"]
        ocr_score = evidence["ocr_score"]
        badge_unreadable = evidence["badge_unreadable"]

        if not ocr.get("present", False):
            if ai_value and ai_value != "+0":
                return "+0", "No badge detected; AI disagreement rejected"
            return "+0", "No badge detected"

        if ocr_score >= 0.9 and ocr_value != "+0":
            return ocr_value, "OCR high confidence"
        if ai_value and ai_value != "+0":
            if badge_unreadable:
                return ai_value, "AI verified unreadable badge"
            return ai_value, "AI verified low-confidence OCR"
        if ocr_score >= 0.4 and ocr_value != "+0":
            return ocr_value, "OCR fallback"
        if badge_unreadable:
            return "+0", "Badge unreadable"
        return "+0", "Fallback"

    @staticmethod
    def _enhancement_evidence(results):
        ocr = results.get("enhance_ocr", {})
        ai_data = ScanOrchestrator._safe_json(results.get("enhance", "{}"))
        ai_value = ScanOrchestrator._parse_enhancement_value(
            ai_data.get("enhance", ai_data.get("level", ""))
        )
        ocr_value = ScanOrchestrator._parse_enhancement_value(ocr.get("value", ""))
        badge_present = bool(ocr.get("present", False))
        badge_readable = bool(ocr.get("readable", False))
        badge_unreadable = badge_present and (
            not badge_readable
            or not ocr_value
            or (ocr_value == "+0" and float(ocr.get("score", 0.0)) < 0.9)
        )
        return {
            "ocr": ocr,
            "ocr_value": ocr_value or "+0",
            "ocr_score": float(ocr.get("score", 0.0)),
            "ai_value": ai_value,
            "raw_ai_json": results.get("enhance", "{}"),
            "badge_present": badge_present,
            "badge_unreadable": badge_unreadable,
        }

    @staticmethod
    def _resolve_choice(raw_ai_json, ai_key, ocr_candidates, allowed, aliases, default_value, ignored_words=None):
        ai_data = ScanOrchestrator._safe_json(raw_ai_json)
        ai_raw = str(ai_data.get(ai_key, ""))
        ai_candidate = ScanOrchestrator._best(
            rank_options(ai_raw, allowed, aliases=aliases, limit=1, ignored_words=ignored_words)
        )
        ocr_candidate = ScanOrchestrator._best(ocr_candidates)
        ocr_score = ocr_candidate["score"] if ocr_candidate else 0.0

        if ocr_candidate and ocr_score >= 0.92:
            return ocr_candidate["value"], "OCR high confidence"
        if ai_candidate and ai_candidate["score"] >= 0.65:
            if not ocr_candidate or ocr_score < 0.82 or ai_candidate["value"] == ocr_candidate["value"]:
                return ai_candidate["value"], "AI verified"
        if ocr_candidate and ocr_score >= 0.5:
            return ocr_candidate["value"], "OCR fallback"
        return default_value, "Default fallback"

    @staticmethod
    def _resolve_stat(raw_ocr, raw_ai_name, ai_value, allowed_stats):
        ocr_candidates = rank_stat_candidates(raw_ocr, allowed_stats, ai_value)
        ai_candidates = rank_stat_candidates(raw_ai_name, allowed_stats, ai_value)
        ocr_best = ScanOrchestrator._best(ocr_candidates)
        ai_best = ScanOrchestrator._best(ai_candidates)
        ocr_score = ocr_best["score"] if ocr_best else 0.0

        if ocr_best and ocr_score >= 0.9:
            return ocr_best["value"], "OCR high confidence", ocr_candidates
        if ai_best and ai_best["score"] >= 0.55:
            if not ocr_best or ocr_score < 0.82 or ai_best["value"] == ocr_best["value"]:
                return ai_best["value"], "AI verified", ocr_candidates
        if ocr_best and ocr_score >= 0.35:
            return ocr_best["value"], "OCR fallback", ocr_candidates
        return "", "Unresolved", ocr_candidates

    @staticmethod
    def _resolve_sub_stat(ocr_line, ai_name):
        ocr_candidates = ocr_line.get("candidates", [])
        ocr_best = ScanOrchestrator._best(ocr_candidates)
        if ocr_best and ocr_best["score"] >= 0.35:
            return ocr_best["value"], "OCR line", ocr_candidates

        ai_candidates = rank_stat_candidates(ai_name, ALL_STATS)
        ai_best = ScanOrchestrator._best(ai_candidates)
        if ai_best and ai_best["score"] >= 0.55:
            return ai_best["value"], "AI fallback", ocr_candidates

        return "", "Unresolved", ocr_candidates

    @staticmethod
    def _parse_results(results):
        parsed_data = {}
        debug_log = []

        enhance_value, enhance_source = ScanOrchestrator._resolve_enhancement(results)
        parsed_data["enhance"] = enhance_value
        parsed_data["_enhance_evidence"] = {
            **ScanOrchestrator._enhancement_evidence(results),
            "source": enhance_source,
            "final_value": enhance_value,
        }
        debug_log.append(format_debug_enhancement(
            enhance_source,
            results.get("enhance_ocr", {}),
            results.get("enhance", "{}"),
            enhance_value,
        ))

        raw_slot_ai = results.get("slot", "{}")
        raw_slot_ocr = results.get("raw_slot_ocr", "")
        parsed_data["slot"], slot_source = ScanOrchestrator._resolve_choice(
            raw_slot_ai,
            "slot",
            results.get("slot_ocr_candidates", []),
            ALL_SLOTS,
            SLOT_ALIASES,
            "Weapon",
            GEAR_CONTEXT_WORDS,
        )
        debug_log.append(format_debug_choice(
            "SLOT",
            slot_source,
            raw_slot_ocr,
            results.get("slot_ocr_candidates", []),
            raw_slot_ai,
            parsed_data["slot"],
        ))

        raw_set_ai = results.get("set", "{}")
        raw_set_ocr = results.get("raw_set_ocr", "")
        parsed_data["set"], set_source = ScanOrchestrator._resolve_choice(
            raw_set_ai,
            "set",
            results.get("set_ocr_candidates", []),
            ALL_SETS,
            SET_ALIASES,
            "Speed Set",
        )
        debug_log.append(format_debug_choice(
            "SET",
            set_source,
            raw_set_ocr,
            results.get("set_ocr_candidates", []),
            raw_set_ai,
            parsed_data["set"],
        ))

        raw_main_text = results.get("raw_main", "")
        raw_ai_main_json = results.get("main_stat", "{}")
        main_data = ScanOrchestrator._safe_json(raw_ai_main_json)
        ai_main_name = main_data.get("name", "")
        ai_main_value = str(main_data.get("value", ""))
        current_slot = parsed_data.get("slot", "Weapon")

        if current_slot == "Weapon":
            parsed_data["main_stat"] = "Flat Attack"
            main_source = "Slot fixed main stat"
            main_candidates = [{"value": "Flat Attack", "score": 1.0, "matched_alias": "Weapon fixed main"}]
        elif current_slot == "Helmet":
            parsed_data["main_stat"] = "Flat Health"
            main_source = "Slot fixed main stat"
            main_candidates = [{"value": "Flat Health", "score": 1.0, "matched_alias": "Helmet fixed main"}]
        elif current_slot == "Armor":
            parsed_data["main_stat"] = "Flat Defense"
            main_source = "Slot fixed main stat"
            main_candidates = [{"value": "Flat Defense", "score": 1.0, "matched_alias": "Armor fixed main"}]
        else:
            valid_mains = SLOT_MAIN_STATS.get(current_slot, ALL_STATS)
            main_value, main_source, main_candidates = ScanOrchestrator._resolve_stat(
                raw_main_text,
                ai_main_name,
                ai_main_value,
                valid_mains,
            )
            parsed_data["main_stat"] = main_value if main_value in valid_mains else ""

        debug_log.append(format_debug_main_stat_details(
            current_slot,
            main_source,
            raw_main_text,
            main_candidates,
            raw_ai_main_json,
            parsed_data["main_stat"],
        ))

        raw_subs_text = results.get("raw_subs", "")
        raw_ai_subs_json = results.get("subs", "{}")
        subs_data = ScanOrchestrator._safe_json(raw_ai_subs_json)
        ai_subs = subs_data.get("substats", [])
        if not isinstance(ai_subs, list):
            ai_subs = []

        parsed_data["subs"] = []
        sub_ocr_candidates = results.get("sub_ocr_candidates", [])

        for idx in range(4):
            ai_item = ai_subs[idx] if idx < len(ai_subs) and isinstance(ai_subs[idx], dict) else {}
            ai_name = str(ai_item.get("name", ""))
            ai_val = str(ai_item.get("value", ""))
            ocr_line = sub_ocr_candidates[idx] if idx < len(sub_ocr_candidates) else {}
            ocr_raw = ocr_line.get("raw", "")

            final_sub_name, sub_source, sub_candidates = ScanOrchestrator._resolve_sub_stat(ocr_line, ai_name)
            ocr_value = ocr_line.get("value")
            ai_value = ScanOrchestrator._extract_numeric_value(ai_val)
            ocr_has_match = bool(sub_candidates and sub_candidates[0]["score"] >= 0.35)
            val_cleaned = ocr_value if ocr_has_match and ocr_value else ai_value or "0"

            parsed_data["subs"].append({
                "stat": final_sub_name if final_sub_name in ALL_STATS else "",
                "val": val_cleaned,
            })

        debug_log.append(format_debug_subs(
            raw_subs_text,
            raw_ai_subs_json,
            parsed_data["subs"],
            sub_ocr_candidates,
        ))

        return parsed_data, "\n".join(debug_log)
