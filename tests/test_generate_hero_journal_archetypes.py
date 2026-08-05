import unittest

from scripts.generate_hero_journal_archetypes import (
    build_signature,
    estimate_build_investment,
    functional_archetype,
    filter_reviewed_stat_groups,
    resolve_scaling,
    select_stat_block,
)


BASE = {
    "atk": 1000,
    "def": 700,
    "hp": 6000,
    "spd": 110,
    "chc": 0.15,
    "chd": 1.5,
    "eff": 0,
    "efr": 0,
}


class HeroJournalArchetypeTests(unittest.TestCase):
    def test_final_stat_column_beats_the_aligned_bonus_column(self):
        final = [2000, 1400, 18000, 230, 100, 300, 80, 50]
        bonus = [900, 700, 9000, 120, 85, 150, 80, 50]
        tokens = []
        for index, value in enumerate(final):
            tokens.append({"value": float(value), "x": 200, "right": 250, "y": 100 + 30 * index})
        for index, value in enumerate(bonus):
            tokens.append({"value": float(value), "x": 300, "right": 350, "y": 100 + 30 * index})

        result = select_stat_block(tokens, BASE, 600)

        self.assertEqual(list(result["values"].values()), final)

    def test_signature_uses_exact_build_investment(self):
        investment = estimate_build_investment({
            "Attack": 1850,
            "Defense": 1010,
            "Health": 16000,
            "Speed": 230,
            "Critical Hit Chance": 15,
            "Critical Hit Damage": 150,
            "Effectiveness": 80,
            "Effect Resistance": 0,
        }, BASE)

        self.assertEqual(
            build_signature(investment),
            ("Effectiveness", "Health", "Speed"),
        )

    def test_signature_does_not_promote_an_uninvested_third_stat(self):
        investment = {stat: 0 for stat in (
            "Attack", "Defense", "Health", "Speed", "Critical Hit Chance",
            "Critical Hit Damage", "Effectiveness", "Effect Resistance",
        )}
        investment.update({"Attack": 8, "Speed": 8})

        self.assertEqual(build_signature(investment), ("Attack", "Speed"))

    def test_manual_review_can_replace_the_inferred_signature(self):
        investment = {stat: 99 for stat in BASE}

        self.assertEqual(
            build_signature(investment, ["Effectiveness", "Health", "Defense", "Speed"]),
            ("Defense", "Effectiveness", "Health", "Speed"),
        )

    def test_attack_debuffers_share_one_functional_archetype(self):
        with_defense = functional_archetype(
            ("Attack", "Defense", "Effectiveness", "Health", "Speed"),
            "Attack",
            "Bruiser",
        )
        without_defense = functional_archetype(
            ("Attack", "Effectiveness", "Health", "Speed"),
            "Attack",
            "Bruiser",
        )

        self.assertEqual(with_defense, without_defense)
        self.assertEqual(with_defense["name"], "Fast Attack-Scaling Debuffer")
        self.assertIn(("Health", "Defense"), with_defense["substatGroups"])

    def test_speed_scaling_template_does_not_duplicate_speed_slot(self):
        archetype = functional_archetype(
            ("Critical Hit Chance", "Critical Hit Damage", "Speed"),
            "Speed",
            "DPS",
        )

        self.assertEqual(archetype["substatGroups"].count(("Speed",)), 1)
        self.assertIn(("Attack",), archetype["substatGroups"])

    def test_crit_debuffer_does_not_become_an_attack_bruiser(self):
        archetype = functional_archetype(
            ("Critical Hit Chance", "Defense", "Effectiveness", "Health", "Speed"),
            "Attack",
            "Bruiser",
        )

        self.assertEqual(archetype["name"], "Fast Crit-Chance Debuffer")
        self.assertNotIn(("Attack",), archetype["substatGroups"])

    def test_manual_review_can_override_reported_scaling(self):
        self.assertEqual(
            resolve_scaling(("Attack", "Speed"), "Speed", "Attack"),
            "Attack",
        )

    def test_manual_desired_stats_remove_template_added_speed(self):
        groups = [("Attack",), ("Speed",), ("Health", "Defense")]

        self.assertEqual(
            filter_reviewed_stat_groups(groups, ["Attack", "Health", "Defense"]),
            [("Attack",), ("Health", "Defense")],
        )

    def test_pure_bulk_shapes_are_tanks(self):
        fast = functional_archetype(("Defense", "Health", "Speed"), "Health", "Bruiser")
        slow_er = functional_archetype(
            ("Defense", "Effect Resistance", "Health"),
            "Health",
            "Bruiser",
        )

        self.assertEqual(fast["name"], "Fast Tank")
        self.assertEqual(slow_er["name"], "ER Bulk Tank")
        self.assertNotIn(("Speed",), slow_er["substatGroups"])


if __name__ == "__main__":
    unittest.main()
