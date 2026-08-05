import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("cv2", types.SimpleNamespace())
sys.modules.setdefault(
    "pytesseract",
    types.SimpleNamespace(
        pytesseract=types.SimpleNamespace(tesseract_cmd=""),
        image_to_string=lambda *args, **kwargs: "",
    ),
)
sys.modules.setdefault("requests", types.SimpleNamespace())

from src.core.enhancement_automator import AutomationStopped, EnhancementAutomator
from src.core.enhancement_packets import EnhancementPacket
from src.core.live_packet_source import EnhancementPacketTimeout


def event(item_id="gear-1", rolls=("speed",), *, low=False):
    operations = [["att_rate", 0.10]]
    operations.extend(
        [
            ["max_hp_rate", 0.01],
            ["def_rate", 0.01],
            ["acc", 0.01],
            ["res", 0.01],
        ]
        if low
        else [
            ["speed", 8],
            ["cri", 0.10],
            ["cri_dmg", 0.14],
            ["att_rate", 0.18],
        ]
    )
    operations.extend([
        [roll, 1 if roll == "speed" else 0.01]
        for roll in rolls
    ])
    return EnhancementPacket.from_message({
        "equip": item_id,
        "op": operations,
    })


class FakePacketSource:
    def __init__(self, events=()):
        self.events = list(events)
        self.started = False
        self.stopped = False
        self.waits = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait_for_enhancement(self, *, expected_item_id, timeout, cancel_check):
        self.waits.append(expected_item_id)
        if cancel_check():
            raise EnhancementPacketTimeout("cancelled")
        if not self.events:
            raise EnhancementPacketTimeout("timeout")
        return self.events.pop(0)


class FakeBackend:
    name = "ADB"

    def __init__(self):
        self.clicks = []

    def click_point(self, point):
        self.clicks.append(point)


def settings():
    return {
        "regions": {"set": {}},
        "click_points": {
            "auto_select": {"x": 1, "y": 1},
            "enhance": {"x": 2, "y": 2},
            "probe_ingredient": {"x": 90, "y": 90},
            "probe_select": {"x": 100, "y": 100},
            "lock": {"x": 3, "y": 103},
            "destroy": {"x": 4, "y": 4},
            "destroy_confirm": {"x": 5, "y": 5},
            "back": {"x": 6, "y": 6},
            "next_piece": {"x": 7, "y": 7},
            "open_enhance": {"x": 8, "y": 8},
            "levels": {f"+{level}": {"x": level, "y": level} for level in (3, 6, 9, 12, 15)},
        },
        "automation": {
            "after_auto_select_seconds": 0,
            "after_level_select_seconds": 0,
            "after_enhance_seconds": 0,
            "after_reward_popup_seconds": 0,
            "after_lock_seconds": 0,
            "after_destroy_seconds": 0,
            "after_destroy_confirm_seconds": 0,
            "after_back_seconds": 0,
            "after_next_piece_seconds": 0,
            "after_open_enhance_seconds": 0,
            "after_enhancement_retry_seconds": 0,
            "enhancement_packet_timeout_seconds": 2,
            "enhancement_read_retries": 2,
        },
    }


def automator(source, backend=None, **overrides):
    def normalize_packet(packet, _enhancement, initial_substats):
        rolls = packet.enhancement_rolls(initial_substats)
        return {
            "enhancementRollStats": [roll.stat_code for roll in rolls],
            "parsedCheckpoints": [
                packet.parsed_gear_at(index * 3, initial_substats)
                for index in range(1, len(rolls) + 1)
            ],
        }

    return EnhancementAutomator(
        settings=settings(),
        allow_destroy=overrides.get("allow_destroy", False),
        max_pieces=overrides.get("max_pieces", 1),
        on_log=overrides.get("on_log", lambda _message: None),
        on_complete=lambda: None,
        on_error=lambda _error: None,
        backend=backend or FakeBackend(),
        packet_source=source,
        item_metadata_resolver=overrides.get(
            "item_metadata_resolver",
            lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 0,
                "initialSubstats": 4,
            },
        ),
        enhancement_normalizer=overrides.get(
            "enhancement_normalizer",
            normalize_packet,
        ),
        cancel_check=overrides.get("cancel_check", lambda: False),
    )


class EnhancementAutomatorTests(unittest.TestCase):
    def test_first_action_uses_one_powder_to_identify_the_piece(self):
        backend = FakeBackend()
        source = FakePacketSource()
        subject = automator(source, backend)

        with self.assertRaises(RuntimeError):
            subject.run()

        self.assertEqual(backend.clicks[:3], [
            settings()["click_points"]["probe_ingredient"],
            settings()["click_points"]["probe_select"],
            settings()["click_points"]["enhance"],
        ])

    def test_missing_packet_keeps_waiting_without_repeating_clicks(self):
        backend = FakeBackend()
        source = FakePacketSource()
        logs = []
        subject = automator(source, backend, on_log=logs.append)

        with self.assertRaises(RuntimeError):
            subject.run()

        self.assertEqual(
            backend.clicks.count(settings()["click_points"]["probe_ingredient"]),
            1,
        )
        self.assertEqual(backend.clicks.count(settings()["click_points"]["enhance"]), 1)
        self.assertIn("continuing to wait", "\n".join(logs))

    def test_fast_packet_waits_for_the_remaining_animation_time(self):
        source = FakePacketSource([event()])
        subject = automator(source)
        subject.settings["automation"]["after_enhance_seconds"] = 2
        sleeps = []
        subject._sleep_seconds = sleeps.append

        with patch(
            "src.core.enhancement_automator.time.monotonic",
            side_effect=(10.0, 10.25),
        ):
            packet = subject._enhance_and_wait(
                3,
                expected_item_id=None,
                piece_number=1,
            )

        self.assertEqual(packet.item_id, "gear-1")
        self.assertAlmostEqual(sleeps[-1], 1.75)

    def test_item_id_is_pinned_after_the_first_enhancement_response(self):
        source = FakePacketSource([
            event(),
            event(rolls=("speed", "cri")),
            event(rolls=("speed", "cri", "acc")),
            event(rolls=("speed", "cri", "acc", "cri_dmg")),
            event(rolls=("speed", "cri", "acc", "cri_dmg", "att_rate")),
        ])
        subject = automator(source, allow_destroy=False)

        result = subject.run()

        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(source.waits, [None, "gear-1", "gear-1", "gear-1", "gear-1"])

    def test_packet_source_always_stops_when_automation_is_cancelled(self):
        source = FakePacketSource()
        subject = automator(source, cancel_check=lambda: True)

        result = subject.run()

        self.assertEqual(result["outcome"], "cancelled")
        self.assertTrue(source.started)
        self.assertTrue(source.stopped)

    def test_four_speed_packet_events_lock_the_piece_at_plus_fifteen(self):
        source = FakePacketSource([
            event(rolls=("speed",)),
            event(rolls=("speed", "speed")),
            event(rolls=("speed", "speed", "speed")),
            event(rolls=("speed", "speed", "speed", "cri")),
            event(rolls=("speed", "speed", "speed", "cri", "speed")),
        ])
        subject = automator(source)

        result = subject.run()

        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["last_decision"]["action"], "lock")
        self.assertIn("4/5", result["last_decision"]["reason"])

    def test_pre_enhanced_item_replays_packet_history_from_imported_level(self):
        source = FakePacketSource([
            event(rolls=("speed", "cri", "acc"), low=True),
        ])
        subject = automator(
            source,
            item_metadata_resolver=lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 6,
                "initialSubstats": 4,
            },
        )

        result = subject.run()

        self.assertEqual(result["last_decision"]["enhancement"], 9)
        self.assertEqual(result["last_decision"]["action"], "destroy")

    def test_injected_packet_normalizer_supplies_checkpoint_data(self):
        source = FakePacketSource([
            event(rolls=("speed",), low=True),
            event(rolls=("speed", "cri"), low=True),
            event(rolls=("speed", "cri", "acc"), low=True),
        ])
        calls = []

        def normalize(packet, enhancement, initial_substats):
            calls.append((packet.item_id, enhancement, initial_substats))
            rolls = packet.enhancement_rolls(initial_substats)
            return {
                "enhancementRollStats": [roll.stat_code for roll in rolls],
                "parsedCheckpoints": [
                    packet.parsed_gear_at(index * 3, initial_substats)
                    for index in range(1, len(rolls) + 1)
                ],
            }

        result = automator(
            source,
            enhancement_normalizer=normalize,
        ).run()

        self.assertEqual(result["last_decision"]["action"], "destroy")
        self.assertEqual(
            calls,
            [
                ("gear-1", None, 4),
                ("gear-1", None, 4),
                ("gear-1", None, 4),
            ],
        )

    def test_missing_imported_item_stops_without_ocr_fallback(self):
        source = FakePacketSource([event()])
        backend = FakeBackend()
        subject = automator(
            source,
            backend,
            item_metadata_resolver=lambda _item_id: None,
        )

        with self.assertRaisesRegex(RuntimeError, "not found in imported inventory"):
            subject.run()

        self.assertEqual(
            backend.clicks[:3],
            [
                settings()["click_points"]["probe_ingredient"],
                settings()["click_points"]["probe_select"],
                settings()["click_points"]["enhance"],
            ],
        )

    def test_stale_imported_enhancement_history_stops(self):
        source = FakePacketSource([event(rolls=("speed", "cri", "acc"))])
        subject = automator(
            source,
            item_metadata_resolver=lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 0,
                "initialSubstats": 4,
            },
        )

        with self.assertRaisesRegex(RuntimeError, "Import a fresh gear.txt"):
            subject.run()

    def test_probe_without_a_checkpoint_roll_targets_plus_three(self):
        source = FakePacketSource([event(rolls=())])
        backend = FakeBackend()
        subject = automator(source, backend)

        with self.assertRaises(RuntimeError):
            subject.run()

        points = settings()["click_points"]
        self.assertEqual(backend.clicks.count(points["levels"]["+3"]), 1)

    def test_plus_three_piece_targets_plus_six_after_probe(self):
        source = FakePacketSource([event(rolls=("speed",))])
        backend = FakeBackend()
        subject = automator(
            source,
            backend,
            item_metadata_resolver=lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 3,
                "initialSubstats": 4,
            },
        )

        with self.assertRaises(RuntimeError):
            subject.run()

        points = settings()["click_points"]
        self.assertEqual(backend.clicks.count(points["levels"]["+3"]), 0)
        self.assertEqual(backend.clicks.count(points["levels"]["+6"]), 1)

    def test_probe_crossing_checkpoint_skips_duplicate_target_click(self):
        source = FakePacketSource([event(rolls=("speed", "cri"))])
        backend = FakeBackend()
        subject = automator(
            source,
            backend,
            item_metadata_resolver=lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 5,
                "initialSubstats": 4,
            },
        )

        with self.assertRaises(RuntimeError):
            subject.run()

        points = settings()["click_points"]
        self.assertEqual(backend.clicks.count(points["levels"]["+6"]), 0)
        self.assertEqual(backend.clicks.count(points["levels"]["+9"]), 1)

    def test_pre_enhanced_probe_replays_server_normalized_history(self):
        packet = event(rolls=("speed", "cri", "acc"), low=True)
        calls = []

        def normalize(value, enhancement, initial_substats):
            calls.append((value.item_id, enhancement, initial_substats))
            rolls = value.enhancement_rolls(initial_substats)
            return {
                "enhancementRollStats": [roll.stat_code for roll in rolls],
                "parsedCheckpoints": [
                    value.parsed_gear_at(index * 3, initial_substats)
                    for index in range(1, len(rolls) + 1)
                ],
            }

        result = automator(
            FakePacketSource([packet]),
            enhancement_normalizer=normalize,
            item_metadata_resolver=lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 8,
                "initialSubstats": 4,
            },
        ).run()

        self.assertEqual(result["last_decision"]["enhancement"], 9)
        self.assertEqual(calls, [("gear-1", None, 4)])

    def test_stop_check_prevents_clicks(self):
        subject = automator(FakePacketSource())
        subject.stop_event.set()

        with self.assertRaises(AutomationStopped):
            subject._click("enhance")


if __name__ == "__main__":
    unittest.main()
