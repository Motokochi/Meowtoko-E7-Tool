import unittest
from unittest.mock import patch

from src.core.enhancement_packets import EnhancementPacket
from src.core.enhancement_rules import (
    AutomationState,
    decide_enhancement_action,
    record_enhancement_event,
)


def packet(enhancement, rolls, subs=None, item_id="gear-1"):
    operations = [["att_rate", 0.10]]
    operations.extend(subs or [["max_hp_rate", 0.03], ["def_rate", 0.03], ["acc", 0.03]])
    operations.extend([[stat, amount] for stat, amount in rolls])
    return EnhancementPacket.from_message({"equip": item_id, "op": operations}).parsed_gear(enhancement)


def observe(state, enhancement, rolls, subs=None):
    parsed = packet(enhancement, rolls, subs)
    record_enhancement_event(parsed, state)
    return decide_enhancement_action(parsed, state)


class EnhancementRulesTests(unittest.TestCase):
    @staticmethod
    def archetype_state(initial_substats=4):
        return AutomationState(
            archetype_context={
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
            },
            initial_substat_count=initial_substats,
        )

    def test_first_plus_three_is_first_roll_even_for_three_substat_gear(self):
        state = AutomationState()
        parsed = packet(3, [("speed", 3)], subs=[
            ["max_hp_rate", 0.12],
            ["cri_dmg", 0.04],
            ["def", 31],
        ])

        record_enhancement_event(parsed, state)

        self.assertEqual(state.roll_stats, ["speed"])
        self.assertEqual(len(parsed["subs"]), 4)

    def test_same_checkpoint_is_never_counted_twice(self):
        state = AutomationState()
        parsed = packet(3, [("speed", 3)])

        record_enhancement_event(parsed, state)
        record_enhancement_event(parsed, state)

        self.assertEqual(state.roll_stats, ["speed"])

    def test_low_gs_piece_continues_while_four_matching_rolls_are_possible(self):
        state = AutomationState()

        first = observe(state, 3, [("speed", 2)])
        second = observe(state, 6, [("speed", 2), ("cri", 0.03)])

        self.assertFalse(state.quality_track)
        self.assertEqual(first.action, "enhance")
        self.assertEqual(second.action, "enhance")
        self.assertEqual(second.next_target, 9)

    def test_low_gs_piece_stops_as_soon_as_four_matching_rolls_are_impossible(self):
        state = AutomationState()
        observe(state, 3, [("speed", 2)])
        observe(state, 6, [("speed", 2), ("cri", 0.03)])

        decision = observe(
            state,
            9,
            [("speed", 2), ("cri", 0.03), ("acc", 0.03)],
        )

        self.assertEqual(decision.action, "destroy")

    def test_four_of_five_matching_rolls_keeps_low_gs_piece(self):
        state = AutomationState()
        events = [
            ("speed", 1),
            ("speed", 1),
            ("speed", 1),
            ("cri", 0.01),
            ("speed", 1),
        ]
        decision = None
        for index in range(1, 6):
            decision = observe(state, index * 3, events[:index])

        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "lock")
        self.assertIn("4/5", decision.reason)

    def test_four_matching_flat_stat_rolls_do_not_keep_low_gs_piece(self):
        state = AutomationState()
        events = [("att", 20)] * 4 + [("speed", 1)]
        decision = None
        for index in range(1, 6):
            decision = observe(state, index * 3, events[:index])

        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "destroy")

    def test_final_low_gs_piece_without_four_matching_rolls_is_destroyed(self):
        state = AutomationState()
        events = [
            ("speed", 1),
            ("speed", 1),
            ("cri", 0.01),
            ("cri", 0.01),
            ("acc", 0.01),
        ]
        decision = None
        for index in range(1, 6):
            parsed = packet(index * 3, events[:index])
            record_enhancement_event(parsed, state)
            decision = decide_enhancement_action(parsed, state)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "destroy")

    def test_quality_track_uses_62_initial_and_58_continuation_thresholds(self):
        state = AutomationState()
        high_subs = [
            ["speed", 8],
            ["cri", 0.10],
            ["cri_dmg", 0.07],
            ["att_rate", 0.08],
        ]

        decision = observe(state, 3, [("att_rate", 0.08)], subs=high_subs)

        self.assertTrue(state.quality_track)
        self.assertEqual(decision.action, "enhance")
        self.assertEqual(decision.next_target, 6)

    def test_third_total_roll_on_the_only_off_stat_destroys(self):
        state = self.archetype_state()
        subs = [
            ["att", 40],
            ["speed", 8],
            ["acc", 0.16],
            ["cri", 0.04],
        ]

        fixture = ({
            "id": "fixture",
            "name": "Fixture",
            "heroes": ["Fixture Hero"],
            "preferredStats": ["Attack", "Effectiveness", "Speed"],
            "flatStatFallbacks": ["Flat Attack"],
            "compatibleSets": ["set.speed"],
        },)
        with patch("src.core.gear_archetypes.load_gear_archetypes", return_value=fixture):
            allowed = observe(state, 3, [("cri", 0.04)], subs=subs)
            rejected = observe(state, 6, [("cri", 0.04), ("cri", 0.04)], subs=subs)

        self.assertEqual(allowed.action, "enhance")
        self.assertEqual(rejected.action, "destroy")
        self.assertIn("3 total rolls", rejected.reason)

    def test_heroic_fourth_substat_starts_at_one_total_roll(self):
        state = self.archetype_state(initial_substats=3)
        parsed = packet(3, [("cri", 0.04)], subs=[
            ["att", 40],
            ["speed", 8],
            ["acc", 0.16],
        ])

        record_enhancement_event(parsed, state)
        decision = decide_enhancement_action(parsed, state)

        self.assertNotEqual(decision.action, "destroy")


if __name__ == "__main__":
    unittest.main()
