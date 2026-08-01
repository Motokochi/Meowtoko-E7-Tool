import sys
import types
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.modules.setdefault("requests", types.SimpleNamespace())
sys.modules.setdefault(
    "pytesseract",
    types.SimpleNamespace(
        pytesseract=types.SimpleNamespace(tesseract_cmd=""),
        image_to_string=lambda *args, **kwargs: "",
    ),
)
sys.modules.setdefault("cv2", types.SimpleNamespace())
from src.core.orchestrator import ScanCancelledError, ScanOrchestrator


class OrchestratorParseTests(unittest.TestCase):
    def test_no_badge_overrides_ai_hallucinated_enhancement(self):
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+0",
                "score": 1.0,
                "present": False,
                "raw_text": "",
                "candidates": [{"value": "+0", "score": 1.0}],
            },
            "enhance": '{"enhance": "+12"}',
            "slot": '{"slot": "Weapon"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Attack", "value": "100"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Weapon",
            "slot_ocr_candidates": [{"value": "Weapon", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Attack",
            "raw_subs": "",
            "sub_ocr_candidates": [],
        })

        self.assertEqual(parsed["enhance"], "+0")

    def test_ai_cannot_escape_allowed_slot_values(self):
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+3",
                "score": 1.0,
                "present": True,
                "raw_text": "+3",
                "candidates": [{"value": "+3", "score": 1.0}],
            },
            "enhance": '{"enhance": "+3"}',
            "slot": '{"slot": "Amulet"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Critical Hit Chance", "value": "55%"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Necklace",
            "slot_ocr_candidates": [{"value": "Necklace", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Critical Hit Chance",
            "raw_subs": "",
            "sub_ocr_candidates": [],
        })

        self.assertEqual(parsed["slot"], "Necklace")
        self.assertEqual(parsed["main_stat"], "Critical Hit Chance")

    def test_enhancement_evidence_preserves_ai_disagreement(self):
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+3",
                "score": 1.0,
                "present": True,
                "raw_text": "3",
                "candidates": [{"value": "+3", "score": 1.0}],
            },
            "enhance": '{"enhance": "+6"}',
            "slot": '{"slot": "Helmet"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Health", "value": "100"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Helmet",
            "slot_ocr_candidates": [{"value": "Helmet", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Health",
            "raw_subs": "",
            "sub_ocr_candidates": [],
        })

        self.assertEqual(parsed["enhance"], "+3")
        self.assertEqual(parsed["_enhance_evidence"]["ai_value"], "+6")
        self.assertEqual(parsed["_enhance_evidence"]["ocr_value"], "+3")

    def test_badge_present_unreadable_ai_zero_is_not_trusted_as_real_zero(self):
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "",
                "score": 0.0,
                "present": True,
                "readable": False,
                "raw_text": "",
                "candidates": [],
            },
            "enhance": '{"enhance": "+0"}',
            "slot": '{"slot": "Boots"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Speed", "value": "17"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Epic Boots",
            "slot_ocr_candidates": [{"value": "Boots", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Speed 17",
            "raw_subs": "",
            "sub_ocr_candidates": [],
        })

        self.assertEqual(parsed["enhance"], "+0")
        self.assertTrue(parsed["_enhance_evidence"]["badge_unreadable"])
        self.assertEqual(parsed["_enhance_evidence"]["source"], "Badge unreadable")

    def test_badge_present_unreadable_accepts_nonzero_ai_value(self):
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "",
                "score": 0.0,
                "present": True,
                "readable": False,
                "raw_text": "",
                "candidates": [],
            },
            "enhance": '{"enhance": "+6"}',
            "slot": '{"slot": "Boots"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Speed", "value": "17"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Epic Boots",
            "slot_ocr_candidates": [{"value": "Boots", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Speed 17",
            "raw_subs": "",
            "sub_ocr_candidates": [],
        })

        self.assertEqual(parsed["enhance"], "+6")
        self.assertTrue(parsed["_enhance_evidence"]["badge_unreadable"])
        self.assertEqual(parsed["_enhance_evidence"]["source"], "AI verified unreadable badge")

    def test_fatal_llm_error_detects_model_architecture_failure(self):
        message = ScanOrchestrator._fatal_llm_error({
            "enhance": json.dumps({
                "error": "llama-server process has terminated: error loading model: unknown model architecture: 'mllama'",
            }),
        })

        self.assertIn("Update Ollama", message)
        self.assertIn("qwen3-vl:8b-instruct", message)

    def test_noisy_slot_text_prefers_helmet_over_weapon(self):
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+0",
                "score": 1.0,
                "present": False,
                "raw_text": "",
                "candidates": [{"value": "+0", "score": 1.0}],
            },
            "enhance": '{"enhance": "+0"}',
            "slot": '{"slot": "Weapon"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Attack", "value": "100"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Otherworldly Epic Helme",
            "slot_ocr_candidates": [{"value": "Helmet", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Attack",
            "raw_subs": "",
            "sub_ocr_candidates": [],
        })

        self.assertEqual(parsed["slot"], "Helmet")
        self.assertEqual(parsed["main_stat"], "Flat Health")

    def test_substat_values_stay_attached_to_ocr_lines(self):
        raw_subs = "Critical Hit Chance 5%\nAttack 4%\nDefense 8%\nHealth 4%"
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+0",
                "score": 1.0,
                "present": False,
                "raw_text": "",
                "candidates": [{"value": "+0", "score": 1.0}],
            },
            "enhance": '{"enhance": "+0"}',
            "slot": '{"slot": "Helmet"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Health", "value": "100"}',
            "subs": '{"substats": [{"name": "Flat Attack", "value": "4"}, {"name": "Critical Hit Chance", "value": "5%"}]}',
            "raw_slot_ocr": "Helmet",
            "slot_ocr_candidates": [{"value": "Helmet", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Health",
            "raw_subs": raw_subs,
            "sub_ocr_candidates": ScanOrchestrator._build_sub_ocr_candidates(raw_subs),
        })

        self.assertEqual(parsed["subs"][0], {"stat": "Critical Hit Chance", "val": "5"})
        self.assertEqual(parsed["subs"][1], {"stat": "Attack", "val": "4"})
        self.assertEqual(parsed["subs"][2], {"stat": "Defense", "val": "8"})
        self.assertEqual(parsed["subs"][3], {"stat": "Health", "val": "4"})

    def test_substat_value_corrects_b_when_ocr_means_eight(self):
        self.assertEqual(ScanOrchestrator._extract_numeric_value("Defense B%"), "8")
        self.assertEqual(ScanOrchestrator._extract_numeric_value("Health 1B%"), "18")
        self.assertEqual(ScanOrchestrator._extract_numeric_value("Attack 2B%"), "28")
        self.assertEqual(ScanOrchestrator._extract_numeric_value("Critical Hit Damage 3B%"), "38")

    def test_substat_parse_uses_corrected_ocr_b_value(self):
        raw_subs = "Defense B%\nHealth 1B%\nAttack 2B%\nCritical Hit Damage 3B%"
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+0",
                "score": 1.0,
                "present": False,
                "raw_text": "",
                "candidates": [{"value": "+0", "score": 1.0}],
            },
            "enhance": '{"enhance": "+0"}',
            "slot": '{"slot": "Helmet"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Health", "value": "100"}',
            "subs": '{"substats": []}',
            "raw_slot_ocr": "Helmet",
            "slot_ocr_candidates": [{"value": "Helmet", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Health",
            "raw_subs": raw_subs,
            "sub_ocr_candidates": ScanOrchestrator._build_sub_ocr_candidates(raw_subs),
        })

        self.assertEqual(parsed["subs"][0], {"stat": "Defense", "val": "8"})
        self.assertEqual(parsed["subs"][1], {"stat": "Health", "val": "18"})
        self.assertEqual(parsed["subs"][2], {"stat": "Attack", "val": "28"})
        self.assertEqual(parsed["subs"][3], {"stat": "Critical Hit Damage", "val": "38"})

    def test_substat_flat_health_is_not_flipped_by_misaligned_ai_percent_value(self):
        raw_subs = (
            "Effect Resistance 7%\n"
            "Health (1) 327 (+164)\n"
            "Critical Hit Damage (1) = 9%(+5%)\n"
            "Speed 3"
        )
        parsed, _ = ScanOrchestrator._parse_results({
            "enhance_ocr": {
                "value": "+0",
                "score": 1.0,
                "present": False,
                "raw_text": "",
                "candidates": [{"value": "+0", "score": 1.0}],
            },
            "enhance": '{"enhance": "+0"}',
            "slot": '{"slot": "Helmet"}',
            "set": '{"set": "Speed Set"}',
            "main_stat": '{"name": "Health", "value": "100"}',
            "subs": '{"substats": [{"name": "Speed", "value": "4"}, {"name": "Critical Hit Chance", "value": "5%"}]}',
            "raw_slot_ocr": "Helmet",
            "slot_ocr_candidates": [{"value": "Helmet", "score": 1.0}],
            "raw_set_ocr": "Speed Set",
            "set_ocr_candidates": [{"value": "Speed Set", "score": 1.0}],
            "raw_main": "Health",
            "raw_subs": raw_subs,
            "sub_ocr_candidates": ScanOrchestrator._build_sub_ocr_candidates(raw_subs),
        })

        self.assertEqual(parsed["subs"][0], {"stat": "Effect Resistance", "val": "7"})
        self.assertEqual(parsed["subs"][1], {"stat": "Flat Health", "val": "327"})
        self.assertEqual(parsed["subs"][2], {"stat": "Critical Hit Damage", "val": "9"})
        self.assertEqual(parsed["subs"][3], {"stat": "Speed", "val": "3"})


class OrchestratorExecutionTests(unittest.TestCase):
    def images(self):
        return {
            name: Image.new("RGB", (8, 8), "black")
            for name in ("enhance", "slot", "main_stat", "set", "subs")
        }

    @patch("src.core.orchestrator.ensure_ollama_running", return_value=False)
    def test_missing_ollama_fails_before_ocr(self, _ensure):
        with self.assertRaisesRegex(RuntimeError, "Could not connect to Ollama"):
            ScanOrchestrator.scan(self.images())

    @patch("src.core.orchestrator.ensure_ollama_running")
    def test_cancel_before_dependency_probe_is_immediate(self, ensure):
        with self.assertRaises(ScanCancelledError):
            ScanOrchestrator.scan(self.images(), cancel_check=lambda: True)
        ensure.assert_not_called()

    def test_controlled_pipeline_reports_progress_and_writes_only_to_selected_directory(self):
        progress = []
        parsed_fixture = {
            "enhance": "+3", "slot": "Weapon", "set": "Speed Set",
            "main_stat": "Flat Attack", "subs": [],
        }
        with tempfile.TemporaryDirectory() as temporary, \
                patch("src.core.orchestrator.ensure_ollama_running", return_value=True), \
                patch("src.core.orchestrator.extract_badge_number_details", return_value={"present": False}), \
                patch("src.core.orchestrator.preprocess_for_ocr", side_effect=lambda image, **_kwargs: image), \
                patch("src.core.orchestrator.read_text_psm7", return_value=""), \
                patch("src.core.orchestrator.read_text_psm6", return_value=""), \
                patch("src.core.orchestrator.query_ollama_vision", return_value="{}"), \
                patch.object(ScanOrchestrator, "_parse_results", return_value=(parsed_fixture, "fixture debug")):
            parsed, debug = ScanOrchestrator.scan(
                self.images(),
                debug_dir=temporary,
                on_progress=lambda *args: progress.append(args),
            )

            self.assertEqual(parsed, parsed_fixture)
            self.assertEqual(debug, "fixture debug")
            self.assertTrue((Path(temporary) / "raw_crop_slot.png").is_file())
            self.assertTrue((Path(temporary) / "debug_enhance_ai.png").is_file())
            self.assertEqual(progress[-1], ("complete", "Gear scan complete.", 1.0))

    def test_timeout_stops_waiting_for_late_llm_results(self):
        def slow_query(*_args, **_kwargs):
            time.sleep(0.15)
            return "{}"

        with tempfile.TemporaryDirectory() as temporary, \
                patch("src.core.orchestrator.ensure_ollama_running", return_value=True), \
                patch("src.core.orchestrator.extract_badge_number_details", return_value={"present": False}), \
                patch("src.core.orchestrator.preprocess_for_ocr", side_effect=lambda image, **_kwargs: image), \
                patch("src.core.orchestrator.read_text_psm7", return_value=""), \
                patch("src.core.orchestrator.read_text_psm6", return_value=""), \
                patch("src.core.orchestrator.query_ollama_vision", side_effect=slow_query):
            with self.assertRaisesRegex(TimeoutError, "Timed out"):
                ScanOrchestrator.scan(self.images(), debug_dir=temporary, timeout_seconds=0.02)

    def test_cancellation_ignores_late_verification_results(self):
        cancelled = threading.Event()

        def query_and_cancel(*_args, **_kwargs):
            cancelled.set()
            return "{}"

        with tempfile.TemporaryDirectory() as temporary, \
                patch("src.core.orchestrator.ensure_ollama_running", return_value=True), \
                patch("src.core.orchestrator.extract_badge_number_details", return_value={"present": False}), \
                patch("src.core.orchestrator.preprocess_for_ocr", side_effect=lambda image, **_kwargs: image), \
                patch("src.core.orchestrator.read_text_psm7", return_value=""), \
                patch("src.core.orchestrator.read_text_psm6", return_value=""), \
                patch("src.core.orchestrator.query_ollama_vision", side_effect=query_and_cancel):
            with self.assertRaises(ScanCancelledError):
                ScanOrchestrator.scan(
                    self.images(), debug_dir=temporary, cancel_check=cancelled.is_set,
                )


if __name__ == "__main__":
    unittest.main()
