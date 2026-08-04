import unittest

from scripts.generate_gear_archetypes import (
    PREFERRED_STAT_EXCEPTIONS,
    archetype_name,
    build_catalog,
    canonical_set,
    dominant_histogram_value,
    estimate_investment,
    preferred_stats,
)


def bins(*values):
    return ",".join(str(value) for value in values)


ABILITY = {
    "att": bins(0, 0, 10, 0, 0, 0, 0, 0, 0, 0),
    "def": bins(10, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "max_hp": bins(0, 10, 0, 0, 0, 0, 0, 0, 0, 0),
    "speed": bins(0, 0, 0, 0, 0, 10, 0, 0, 0, 0),
    "cri": bins(0, 0, 0, 0, 0, 0, 0, 0, 10, 0),
    "cri_dmg": bins(0, 0, 0, 0, 0, 10, 0, 0, 0, 0),
    "acc": bins(10, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "res": bins(10, 0, 0, 0, 0, 0, 0, 0, 0, 0),
}

BASE = {
    "atk": 1000,
    "def": 700,
    "hp": 8000,
    "spd": 100,
    "chc": 0.15,
    "chd": 1.5,
    "eff": 0,
    "efr": 0,
    "role": "assassin",
    "scalingStat": "Health",
}


class GenerateGearArchetypeTests(unittest.TestCase):
    def test_dominant_peak_beats_a_taller_isolated_bin(self):
        counts = bins(30, 0, 0, 25, 25, 0, 0, 0, 0, 0)
        self.assertEqual(dominant_histogram_value(counts, list(range(11))), 3.5)
        with self.assertRaises(ValueError):
            dominant_histogram_value(bins(1, 2), list(range(11)))

    def test_investment_is_relative_to_the_hero_base_stats(self):
        investment = estimate_investment(ABILITY, BASE)
        self.assertAlmostEqual(investment["Attack"], 5.9375)
        self.assertEqual(investment["Defense"], 0)
        self.assertAlmostEqual(investment["Speed"], 22.5)
        self.assertGreater(investment["Critical Hit Chance"], 10)
        self.assertEqual(investment["Effect Resistance"], 0)

    def test_preferred_stats_use_independent_roll_floors(self):
        selected = preferred_stats({
            "Attack": 4.9,
            "Defense": 5,
            "Health": 5,
            "Speed": 3,
            "Critical Hit Chance": 4.9,
            "Critical Hit Damage": 5,
            "Effectiveness": 5,
            "Effect Resistance": 4.9,
        })
        self.assertEqual(
            selected,
            ("Critical Hit Damage", "Defense", "Effectiveness", "Health", "Speed"),
        )

    def test_sparse_histogram_exceptions_have_at_least_three_desired_stats(self):
        self.assertTrue(all(len(stats) >= 3 for stats in PREFERRED_STAT_EXCEPTIONS.values()))
        self.assertEqual(
            PREFERRED_STAT_EXCEPTIONS["Successor Taeyou"],
            ("Attack", "Critical Hit Damage", "Effectiveness"),
        )

    def test_archetype_names_describe_scaling_crit_and_role(self):
        self.assertEqual(
            archetype_name(("Critical Hit Chance", "Defense", "Health"), "Defense", "Bruiser"),
            "Defense-Scaling Bruiser",
        )
        self.assertEqual(
            archetype_name(("Effect Resistance", "Health"), "Attack", "Tank"),
            "ER Tank",
        )
        self.assertEqual(
            archetype_name(("Attack", "Health"), "Attack", "Bruiser"),
            "Non-Crit-Chance Attack-Scaling Bruiser",
        )
        self.assertEqual(
            archetype_name(("Health",), "Health", "Bruiser"),
            "Non-Crit-Chance Health-Scaling Bruiser",
        )
        self.assertEqual(
            archetype_name(("Critical Hit Damage", "Health"), "Health", "Bruiser"),
            "Non-Crit-Chance Health-Scaling Bruiser",
        )

    def test_set_codes_reuse_the_packet_import_catalog(self):
        self.assertEqual(canonical_set("set_revenant"), ("set.reversal", "Reversal Set"))
        with self.assertRaisesRegex(ValueError, "Unknown equipment set code"):
            canonical_set("set_clown")

    def test_catalog_includes_hero_names_without_codes_and_deduplicates_stacked_sets(self):
        snapshot = {
            "season": {
                "season_code": "fixture",
                "name": "Fixture",
                "startDate": "2026-01-01",
                "endDate": "2026-02-01",
            },
            "heroes": [{
                "hero_code": "c1",
                "hero_names": {"c1": "Fixture Hero"},
                "pick_rate": 12.5,
            }],
            "details": [{
                "heroCode": "c1",
                "regDate": "2026-02-02",
                "abillity": ABILITY,
                "equip": [
                    {"equip_list": ["set_max_hp", "set_max_hp", "set_immune"], "rate": 20},
                    {"equip_list": ["set_speed"], "rate": 4},
                ],
            }],
        }
        catalog = build_catalog(snapshot, {"c1": BASE})
        encoded = str(catalog)

        self.assertNotIn("c1", encoded)
        self.assertNotIn("heroCode", encoded)
        self.assertEqual(catalog["archetypes"][0]["heroes"], ["Fixture Hero"])
        self.assertEqual(catalog["archetypes"][0]["name"], "Attack-Scaling DPS")
        self.assertEqual(catalog["source"]["grade"], "emperor")
        evidence = catalog["archetypes"][0]["setEvidence"]
        self.assertEqual({item["averageUsagePercent"] for item in evidence}, {20.0})
        self.assertEqual(catalog["source"]["minimumSetUsagePercent"], 5)


if __name__ == "__main__":
    unittest.main()
