import json
import unittest

from src.core.packet_inventory import normalize_account_inventory
from src.optimizer.data.fribbels import parse_fribbels_gear_bytes


class PacketInventoryTests(unittest.TestCase):
    def test_imports_every_supported_enhancement_level_and_only_five_star_units(self):
        account_data = {
            "equips": {
                "one": {
                    "id": 101,
                    "code": "ecw6n",
                    "f": "set_speed",
                    "g": 5,
                    "l": True,
                    "op": [
                        ["cri_dmg", 0.13],
                        ["speed", 3],
                        ["cri", 0.04],
                        ["max_hp_rate", 0.07],
                        ["def_rate", 0.06],
                        ["speed", 2],
                    ],
                },
                "two": {
                    "id": 102,
                    "code": "ecw6a_u",
                    "f": "set_acc",
                    "g": 4,
                    "op": [
                        ["def", 62],
                        ["speed", 3],
                        ["max_hp_rate", 0.06],
                        ["res", 0.05],
                        ["speed", 2],
                        ["speed", 3],
                        ["speed", 4],
                        ["speed", 2],
                    ],
                },
            },
            "units": {
                "five": {"id": 201, "code": "c5001", "g": 5, "z": 4},
                "four": {"id": 202, "code": "c1017", "g": 4, "z": 3},
            },
        }

        document, skipped = normalize_account_inventory(
            account_data,
            hero_names={"c5001": "Adventurer Ras"},
        )
        parsed = parse_fribbels_gear_bytes(json.dumps(document).encode())

        self.assertEqual(skipped, ())
        self.assertEqual([item.enhance for item in parsed.items], [3, 12])
        self.assertEqual([item.item_level for item in parsed.items], [85, 90])
        self.assertEqual(len(parsed.heroes), 1)
        self.assertEqual(parsed.heroes[0].name, "Adventurer Ras")
        self.assertEqual(parsed.heroes[0].stars, 5)

    def test_gear_without_set_metadata_is_reported_instead_of_guessed(self):
        document, skipped = normalize_account_inventory({
            "equips": {
                "fodder": {
                    "id": 9,
                    "code": "efh05",
                    "g": 4,
                    "op": [["att", 7], ["max_hp", 63]],
                },
            },
            "units": {},
        })

        self.assertEqual(document["items"], [])
        self.assertEqual(skipped[0]["itemId"], "9")
        self.assertIn("set", skipped[0]["reason"])


if __name__ == "__main__":
    unittest.main()
