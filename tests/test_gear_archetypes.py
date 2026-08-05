import unittest
from unittest.mock import patch

from src.core.gear_archetypes import analyze_gear_archetypes


class GearArchetypeTests(unittest.TestCase):
    archetypes = ({
        "id": "fixture-a",
        "name": "Fixture A",
        "heroes": ["Hero A"],
        "preferredStats": ["Attack", "Effectiveness", "Speed"],
        "flatStatFallbacks": ["Flat Attack"],
        "compatibleSets": ["set.speed"],
        "heroesBySet": {"set.speed": ["Hero A"]},
    },)

    def analyze(self, **overrides):
        values = {
            "gear_set": "set.speed",
            "slot": "slot.weapon",
            "main_stat": "item_stat.flat_attack",
            "substats": (
                "item_stat.attack_percent",
                "item_stat.speed",
                "item_stat.effectiveness_percent",
                "item_stat.critical_hit_chance_percent",
            ),
            "roll_counts": {
                "item_stat.attack_percent": 2,
                "item_stat.speed": 2,
                "item_stat.effectiveness_percent": 2,
                "item_stat.critical_hit_chance_percent": 2,
            },
        }
        values.update(overrides)
        with patch("src.core.gear_archetypes.load_gear_archetypes", return_value=self.archetypes):
            return analyze_gear_archetypes(**values)

    def test_three_desired_substats_match_and_third_off_stat_roll_rejects(self):
        accepted = self.analyze()
        rejected = self.analyze(roll_counts={
            "item_stat.attack_percent": 2,
            "item_stat.speed": 2,
            "item_stat.effectiveness_percent": 2,
            "item_stat.critical_hit_chance_percent": 3,
        })

        self.assertEqual(accepted["verdict"], "keep")
        self.assertEqual(rejected["verdict"], "destroy")

    def test_flat_fallback_matches_substat_but_never_right_side_main(self):
        substat_match = self.analyze(substats=(
            "item_stat.flat_attack",
            "item_stat.speed",
            "item_stat.effectiveness_percent",
            "item_stat.critical_hit_chance_percent",
        ))
        main_rejection = self.analyze(
            slot="slot.ring",
            main_stat="item_stat.flat_attack",
        )

        self.assertEqual(substat_match["verdict"], "keep")
        self.assertEqual(main_rejection["verdict"], "destroy")

    def test_any_surviving_archetype_keeps_the_piece(self):
        self.archetypes = (*self.archetypes, {
            "id": "fixture-b",
            "name": "Fixture B",
            "heroes": ["Hero B"],
            "preferredStats": ["Attack", "Critical Hit Chance", "Speed"],
            "flatStatFallbacks": ["Flat Attack"],
            "compatibleSets": ["set.speed"],
        })
        result = self.analyze(substats=(
            "item_stat.attack_percent",
            "item_stat.speed",
            "item_stat.effectiveness_percent",
            "item_stat.critical_hit_chance_percent",
        ), roll_counts={
            "item_stat.attack_percent": 1,
            "item_stat.speed": 2,
            "item_stat.effectiveness_percent": 1,
            "item_stat.critical_hit_chance_percent": 3,
        })

        self.assertEqual(result["verdict"], "keep")
        self.assertTrue(any(match["status"] == "eligible" for match in result["matches"]))

    def test_compatible_heroes_are_scoped_to_the_piece_set(self):
        self.archetypes = ({
            **self.archetypes[0],
            "heroes": ["Hero A", "Hero B"],
            "heroesBySet": {"set.speed": ["Hero B"]},
        },)

        result = self.analyze()

        self.assertEqual(result["matches"][0]["heroes"], ["Hero B"])

    def test_internal_stat_groups_are_not_exposed_in_public_matches(self):
        match = self.analyze()["matches"][0]

        self.assertEqual(set(match), {
            "id",
            "name",
            "heroes",
            "preferredStats",
            "matchingSubstats",
            "offStats",
            "status",
        })

    def test_alternative_bulk_stats_count_as_one_conceptual_slot(self):
        self.archetypes = ({
            **self.archetypes[0],
            "preferredStats": ["Attack", "Speed", "Effectiveness", "Health", "Defense"],
            "flatStatFallbacks": ["Flat Attack", "Flat Health", "Flat Defense"],
            "substatGroups": [
                ["Attack"],
                ["Speed"],
                ["Effectiveness"],
                ["Health", "Defense"],
            ],
        },)

        false_match = self.analyze(substats=(
            "item_stat.attack_percent",
            "item_stat.health_percent",
            "item_stat.defense_percent",
            "item_stat.critical_hit_chance_percent",
        ))
        real_match = self.analyze(substats=(
            "item_stat.attack_percent",
            "item_stat.speed",
            "item_stat.effectiveness_percent",
            "item_stat.critical_hit_chance_percent",
        ))

        self.assertEqual(false_match["verdict"], "destroy")
        self.assertEqual(real_match["verdict"], "keep")


if __name__ == "__main__":
    unittest.main()
