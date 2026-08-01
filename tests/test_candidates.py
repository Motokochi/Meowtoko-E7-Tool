import unittest

from src.extractors.candidates import (
    SET_ALIASES,
    SLOT_ALIASES,
    rank_enhancement_candidates,
    rank_options,
    rank_stat_candidates,
)
from src.constants import ALL_SETS, ALL_SLOTS


class CandidateRankingTests(unittest.TestCase):
    def test_set_ocr_typo_ranks_speed(self):
        candidates = rank_options("Speec Set", ALL_SETS, aliases=SET_ALIASES)
        self.assertEqual(candidates[0]["value"], "Speed Set")
        self.assertGreaterEqual(candidates[0]["score"], 0.5)

    def test_legacy_crit_ranks_critical_set(self):
        candidates = rank_options("Crit", ALL_SETS, aliases=SET_ALIASES)
        self.assertEqual(candidates[0]["value"], "Critical Set")

    def test_slot_ocr_typo_ranks_necklace(self):
        candidates = rank_options("Necklacc", ALL_SLOTS)
        self.assertEqual(candidates[0]["value"], "Necklace")

    def test_slot_ignores_nearby_gear_context_words(self):
        candidates = rank_options("Otherworldly Epic Helme", ALL_SLOTS, aliases=SLOT_ALIASES)
        self.assertEqual(candidates[0]["value"], "Helmet")

    def test_percent_stat_uses_visible_percent(self):
        candidates = rank_stat_candidates("Attack 12%")
        self.assertEqual(candidates[0]["value"], "Attack")

    def test_flat_stat_when_percent_missing(self):
        candidates = rank_stat_candidates("Attack 154")
        self.assertEqual(candidates[0]["value"], "Flat Attack")

    def test_enhancement_candidate_is_clamped_to_known_levels(self):
        candidates = rank_enhancement_candidates("+15")
        self.assertEqual(candidates[0]["value"], "+15")

    def test_enhancement_candidate_corrects_leading_one_read_as_seven(self):
        candidates = rank_enhancement_candidates("+73")
        self.assertEqual(candidates[0]["value"], "+13")
        self.assertGreaterEqual(candidates[0]["score"], 0.9)


if __name__ == "__main__":
    unittest.main()
