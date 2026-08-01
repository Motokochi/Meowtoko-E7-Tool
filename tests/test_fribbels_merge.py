from __future__ import annotations

import ast
import copy
import inspect
import json
import os
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from src.optimizer.data import (
    FRIBBELS_FINGERPRINT_ALGORITHM,
    FRIBBELS_FINGERPRINT_VERSION,
    IDENTITY_KIND_PRIORITY,
    FribbelsIdentityKind,
    FribbelsInventoryItem,
    FribbelsItemIdentity,
    FribbelsMergeInputError,
    FribbelsMergeOutcomeKind,
    FrozenJsonObject,
    fribbels_fingerprint_payload,
    fribbels_item_fingerprint,
    fribbels_item_identities,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
    parse_fribbels_gear_file,
    stable_item_id_from_identity,
    thaw_json,
)
from src.optimizer.data import fribbels_merge as merge_module
from src.optimizer.domain import GearItem, ItemStatType


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"


def _base_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "gear": "Ring",
        "rank": "Epic",
        "set": "HealthSet",
        "enhance": 15,
        "level": 85,
        "main": {"type": "HealthPercent", "value": 60},
        "substats": [
            {"type": "Speed", "value": 12, "rolls": 3},
            {"type": "DefensePercent", "value": 10, "rolls": 2},
        ],
    }
    item.update(overrides)
    return item


def _parse(items: list[object], *, heroes: list[object] | None = None):
    payload: dict[str, object] = {"items": items, "heroes": [] if heroes is None else heroes}
    return parse_fribbels_gear_bytes(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _single_item(**overrides: object):
    result = _parse([_base_item(**overrides)])
    if result.accepted_count != 1:
        raise AssertionError(result.rejections)
    return result.items[0]


class FribbelsIdentityContractTests(unittest.TestCase):
    def test_identity_priority_is_ingame_source_then_fingerprint(self) -> None:
        item = _single_item(ingameId=123, id="source-123")
        identities = fribbels_item_identities(item)

        self.assertEqual(
            tuple(identity.kind for identity in identities),
            IDENTITY_KIND_PRIORITY,
        )
        self.assertEqual(identities[0].value, "123")
        self.assertEqual(identities[1].value, "source-123")
        self.assertRegex(identities[2].value, r"^[0-9a-f]{64}$")
        self.assertEqual(
            stable_item_id_from_identity(identities[0]),
            "fribbels:item:ingame:123",
        )

    def test_source_and_fingerprint_are_used_when_stronger_ids_are_absent(self) -> None:
        source_only = fribbels_item_identities(_single_item(id="source-only"))
        fingerprint_only = fribbels_item_identities(_single_item())

        self.assertEqual(
            tuple(identity.kind for identity in source_only),
            (FribbelsIdentityKind.SOURCE, FribbelsIdentityKind.FINGERPRINT),
        )
        self.assertEqual(
            tuple(identity.kind for identity in fingerprint_only),
            (FribbelsIdentityKind.FINGERPRINT,),
        )
        self.assertTrue(
            stable_item_id_from_identity(fingerprint_only[0], occurrence=1).endswith(":1")
        )

    def test_equal_raw_text_is_separate_across_identity_namespaces(self) -> None:
        ingame = FribbelsItemIdentity(FribbelsIdentityKind.INGAME, "same:value")
        source = FribbelsItemIdentity(FribbelsIdentityKind.SOURCE, "same:value")

        self.assertNotEqual(ingame, source)
        self.assertNotEqual(ingame.namespaced_key, source.namespaced_key)
        self.assertEqual(
            stable_item_id_from_identity(ingame),
            "fribbels:item:ingame:same%3Avalue",
        )
        self.assertEqual(
            stable_item_id_from_identity(source),
            "fribbels:item:source:same%3Avalue",
        )

    def test_fingerprint_payload_is_explicit_versioned_and_sorted(self) -> None:
        item = _single_item(
            main={"type": "HealthPercent", "value": 60.0, "reforgedValue": 65},
            substats=[
                {
                    "type": "DefensePercent",
                    "value": 10,
                    "rolls": 2,
                    "modified": False,
                    "reforgedValue": 12,
                },
                {
                    "type": "Speed",
                    "value": 12.0,
                    "rolls": 3,
                    "ingameRolls": 3,
                    "reforgedValue": 14,
                },
            ],
        )

        payload = thaw_json(fribbels_fingerprint_payload(item))

        self.assertEqual(FRIBBELS_FINGERPRINT_VERSION, 1)
        self.assertEqual(FRIBBELS_FINGERPRINT_ALGORITHM, "sha256")
        self.assertEqual(payload["fingerprintVersion"], 1)
        self.assertEqual(payload["slot"], "slot.ring")
        self.assertEqual(payload["set"], "set.health")
        self.assertEqual(payload["rank"], "rank.epic")
        self.assertEqual(payload["main"]["value"], 60)
        self.assertEqual(
            [stat["type"] for stat in payload["substats"]],
            [
                "item_stat.defense_percent",
                "item_stat.speed",
            ],
        )
        self.assertEqual(payload["substats"][1]["value"], 12)

    def test_fingerprint_ignores_order_owner_lock_names_material_and_unknowns(self) -> None:
        base = _base_item(
            name="Fixture One",
            material="Hunt",
            locked=False,
            ingameEquippedId="hero-one",
            futureUnknown={"revision": 1},
        )
        changed = copy.deepcopy(base)
        changed["name"] = "Fixture Renamed"
        changed["material"] = "Conversion"
        changed["locked"] = True
        changed["ingameEquippedId"] = "hero-two"
        changed["futureUnknown"] = {"revision": 2}
        changed["substats"] = list(reversed(changed["substats"]))
        changed["augmentedStats"] = {"broken": True}
        heroes = [{"id": "hero-one"}, {"id": "hero-two"}]

        first = _parse([base], heroes=heroes).items[0]
        second_result = _parse([changed], heroes=heroes)
        second = second_result.items[0]

        self.assertEqual(fribbels_item_fingerprint(first), fribbels_item_fingerprint(second))
        self.assertNotEqual(first.projection.augmented_evidence, second.projection.augmented_evidence)
        self.assertGreater(second_result.warning_count, 0)

    def test_contribution_or_roll_evidence_changes_the_fingerprint(self) -> None:
        original = _single_item()
        changed_value = _single_item(
            substats=[
                {"type": "Speed", "value": 13, "rolls": 3},
                {"type": "DefensePercent", "value": 10, "rolls": 2},
            ]
        )
        changed_rolls = _single_item(
            substats=[
                {"type": "Speed", "value": 12, "rolls": 4},
                {"type": "DefensePercent", "value": 10, "rolls": 2},
            ]
        )

        self.assertNotEqual(fribbels_item_fingerprint(original), fribbels_item_fingerprint(changed_value))
        self.assertNotEqual(fribbels_item_fingerprint(original), fribbels_item_fingerprint(changed_rolls))

    def test_fingerprint_is_equal_across_fresh_python_process_hash_seeds(self) -> None:
        payload = json.dumps(
            {"items": [_base_item()], "heroes": []},
            separators=(",", ":"),
        )
        code = (
            "import sys;"
            "from src.optimizer.data import parse_fribbels_gear_bytes,fribbels_item_fingerprint;"
            "item=parse_fribbels_gear_bytes(sys.argv[1].encode('utf-8')).items[0];"
            "print(fribbels_item_fingerprint(item))"
        )
        outputs = []
        for seed in ("1", "987654"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            completed = subprocess.run(
                [sys.executable, "-c", code, payload],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            outputs.append(completed.stdout.strip())

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], fribbels_item_fingerprint(_single_item()))

    def test_invalid_fingerprint_occurrence_contract_is_rejected(self) -> None:
        identity = fribbels_item_identities(_single_item())[-1]
        with self.assertRaises(FribbelsMergeInputError):
            stable_item_id_from_identity(identity)
        with self.assertRaises(FribbelsMergeInputError):
            stable_item_id_from_identity(identity, occurrence=0)
        with self.assertRaises(FribbelsMergeInputError):
            stable_item_id_from_identity(
                FribbelsItemIdentity(FribbelsIdentityKind.INGAME, "1"),
                occurrence=1,
            )


class FribbelsReimportMergeTests(unittest.TestCase):
    def test_same_scanner_import_twice_is_idempotent(self) -> None:
        parsed = parse_fribbels_gear_file(FIXTURES / "valid-scanner-export-utf8.txt")

        first = merge_fribbels_inventory((), parsed)
        second = merge_fribbels_inventory(first.items, parsed)

        self.assertEqual(len(first.inserted), 2)
        self.assertEqual(len(first.updated), 0)
        self.assertEqual(len(second.inserted), 0)
        self.assertEqual(len(second.updated), 0)
        self.assertEqual(len(second.unchanged), 2)
        self.assertEqual(second.conflicts, ())
        self.assertEqual(second.items, first.items)
        self.assertEqual(second.unseen_existing_ids, ())

    def test_matching_strong_id_updates_source_state_and_preserves_user_metadata(self) -> None:
        initial = _parse(
            [_base_item(ingameId="item-one", locked=False, ingameEquippedId="hero-one")],
            heroes=[{"id": "hero-one"}, {"id": "hero-two"}],
        )
        first = merge_fribbels_inventory((), initial)
        original = replace(
            first.items[0],
            user_metadata={"favorite": True, "tags": ["keep", {"score": 9}]},
        )
        changed = _parse(
            [
                _base_item(
                    ingameId="item-one",
                    locked=True,
                    ingameEquippedId="hero-two",
                    substats=[
                        {"type": "Speed", "value": 14, "rolls": 4},
                        {"type": "DefensePercent", "value": 10, "rolls": 2},
                    ],
                )
            ],
            heroes=[{"id": "hero-one"}, {"id": "hero-two"}],
        )

        merged = merge_fribbels_inventory((original,), changed)
        updated = merged.items[0]

        self.assertEqual(len(merged.updated), 1)
        self.assertEqual(len(merged.items), 1)
        self.assertEqual(updated.stable_item_id, original.stable_item_id)
        self.assertEqual(updated.gear_item.item_id, original.stable_item_id)
        self.assertEqual(updated.gear_item.equipped_hero_id, "hero-two")
        self.assertTrue(updated.gear_item.locked)
        self.assertEqual(dict(updated.gear_item.substats)[ItemStatType.SPEED], 14)
        self.assertIs(updated.user_metadata, original.user_metadata)
        self.assertEqual(updated.user_metadata, original.user_metadata)
        self.assertNotEqual(updated.source_metadata, original.source_metadata)
        self.assertNotEqual(updated.fingerprint, original.fingerprint)
        self.assertEqual(
            [identity for identity in updated.identities if identity.kind is FribbelsIdentityKind.INGAME],
            [FribbelsItemIdentity(FribbelsIdentityKind.INGAME, "item-one")],
        )
        with self.assertRaises(TypeError):
            updated.user_metadata["new"] = True  # type: ignore[index]

    def test_later_source_and_ingame_aliases_keep_the_fingerprint_stable_id(self) -> None:
        fingerprint_only = merge_fribbels_inventory((), _parse([_base_item()]))
        initial_id = fingerprint_only.items[0].stable_item_id
        with_source = merge_fribbels_inventory(
            fingerprint_only.items,
            _parse([_base_item(id="source-one")]),
        )
        with_ingame = merge_fribbels_inventory(
            with_source.items,
            _parse([_base_item(id="source-one", ingameId="ingame-one")]),
        )

        self.assertEqual(with_source.updated[0].stable_item_id, initial_id)
        self.assertEqual(with_ingame.updated[0].stable_item_id, initial_id)
        self.assertEqual(with_ingame.items[0].stable_item_id, initial_id)
        self.assertEqual(
            tuple(identity.kind for identity in with_ingame.items[0].identities),
            IDENTITY_KIND_PRIORITY,
        )
        self.assertEqual(with_ingame.items[0].current_ingame_id, "ingame-one")
        self.assertEqual(with_ingame.items[0].current_source_id, "source-one")

    def test_items_only_reimport_keeps_historical_strong_aliases(self) -> None:
        initial = merge_fribbels_inventory(
            (),
            _parse([_base_item(ingameId="ingame-one", id="source-one")]),
        )
        items_only = merge_fribbels_inventory(initial.items, _parse([_base_item()]))
        state = items_only.items[0]

        self.assertEqual(state.stable_item_id, initial.items[0].stable_item_id)
        self.assertIsNone(state.current_ingame_id)
        self.assertIsNone(state.current_source_id)
        self.assertIn(
            FribbelsItemIdentity(FribbelsIdentityKind.INGAME, "ingame-one"),
            state.identities,
        )
        self.assertIn(
            FribbelsItemIdentity(FribbelsIdentityKind.SOURCE, "source-one"),
            state.identities,
        )

    def test_identical_fingerprint_only_items_keep_multiplicity_across_row_reordering(self) -> None:
        first_payload = _parse(
            [
                _base_item(ingameEquippedId="hero-a", locked=False),
                _base_item(ingameEquippedId="hero-b", locked=True),
            ],
            heroes=[{"id": "hero-a"}, {"id": "hero-b"}],
        )
        first = merge_fribbels_inventory((), first_payload)
        owner_to_id = {
            item.gear_item.equipped_hero_id: item.stable_item_id for item in first.items
        }
        reordered = _parse(
            [
                _base_item(ingameEquippedId="hero-b", locked=True),
                _base_item(ingameEquippedId="hero-a", locked=False),
            ],
            heroes=[{"id": "hero-a"}, {"id": "hero-b"}],
        )

        second = merge_fribbels_inventory(first.items, reordered)

        self.assertEqual(len(first.items), 2)
        self.assertEqual(len({item.fingerprint for item in first.items}), 1)
        self.assertEqual(len({item.stable_item_id for item in first.items}), 2)
        self.assertTrue(all(item.stable_item_id.endswith((":1", ":2")) for item in first.items))
        self.assertEqual(len(second.unchanged), 2)
        self.assertEqual(
            {item.gear_item.equipped_hero_id: item.stable_item_id for item in second.items},
            owner_to_id,
        )

    def test_fully_indistinguishable_duplicates_keep_the_same_stable_id_set(self) -> None:
        parsed = _parse([_base_item(), copy.deepcopy(_base_item())])
        first = merge_fribbels_inventory((), parsed)
        second = merge_fribbels_inventory(first.items, parsed)

        self.assertEqual(len(first.items), 2)
        self.assertEqual(len(second.unchanged), 2)
        self.assertEqual(
            {item.stable_item_id for item in first.items},
            {item.stable_item_id for item in second.items},
        )

    def test_differently_identified_but_stat_identical_items_do_not_collapse(self) -> None:
        parsed = _parse(
            [
                _base_item(ingameId="same-text"),
                _base_item(id="same-text"),
            ]
        )

        merged = merge_fribbels_inventory((), parsed)

        self.assertEqual(len(merged.inserted), 2)
        self.assertEqual(len(merged.items), 2)
        self.assertEqual(len({item.fingerprint for item in merged.items}), 1)
        self.assertEqual(
            {item.stable_item_id for item in merged.items},
            {
                "fribbels:item:ingame:same-text",
                "fribbels:item:source:same-text",
            },
        )

    def test_duplicate_incoming_strong_claim_is_an_actionable_conflict(self) -> None:
        parsed = _parse(
            [
                _base_item(ingameId="duplicate"),
                _base_item(
                    ingameId="duplicate",
                    substats=[
                        {"type": "Speed", "value": 13, "rolls": 3},
                        {"type": "DefensePercent", "value": 10, "rolls": 2},
                    ],
                ),
            ]
        )

        merged = merge_fribbels_inventory((), parsed)

        self.assertEqual(len(merged.inserted), 1)
        self.assertEqual(len(merged.conflicts), 1)
        self.assertEqual(merged.conflicts[0].source_index, 1)
        self.assertEqual(merged.conflicts[0].code, "duplicate-incoming-identity")
        self.assertNotIn("duplicate", merged.conflicts[0].message)

    def test_aliases_resolving_to_two_existing_items_are_a_conflict(self) -> None:
        existing = merge_fribbels_inventory(
            (),
            _parse(
                [
                    _base_item(ingameId="ingame-a", id="source-a"),
                    _base_item(ingameId="ingame-b", id="source-b"),
                ]
            ),
        )
        incoming = _parse([_base_item(ingameId="ingame-a", id="source-b")])

        merged = merge_fribbels_inventory(existing.items, incoming)

        self.assertEqual(len(merged.conflicts), 1)
        self.assertEqual(merged.conflicts[0].code, "conflicting-existing-aliases")
        self.assertEqual(merged.items, existing.items)
        self.assertEqual(set(merged.unseen_existing_ids), {item.stable_item_id for item in existing.items})

    def test_unrelated_preexisting_stable_id_collision_is_not_overwritten(self) -> None:
        initial = merge_fribbels_inventory(
            (),
            _parse([_base_item(ingameId="old-id")]),
        ).items[0]
        colliding_id = "fribbels:item:ingame:new-id"
        colliding_gear = GearItem(
            item_id=colliding_id,
            slot=initial.gear_item.slot,
            gear_set=initial.gear_item.gear_set,
            main_stat=initial.gear_item.main_stat,
            main_stat_value=initial.gear_item.main_stat_value,
            substats=initial.gear_item.substats,
            item_level=initial.gear_item.item_level,
            enhance=initial.gear_item.enhance,
        )
        unrelated = replace(
            initial,
            stable_item_id=colliding_id,
            gear_item=colliding_gear,
        )
        incoming = _parse(
            [
                _base_item(
                    ingameId="new-id",
                    substats=[
                        {"type": "Speed", "value": 15, "rolls": 4},
                        {"type": "DefensePercent", "value": 10, "rolls": 2},
                    ],
                )
            ]
        )

        merged = merge_fribbels_inventory((unrelated,), incoming)

        self.assertEqual(merged.items, (unrelated,))
        self.assertEqual(merged.conflicts[0].code, "stable-id-collision")

    def test_rejected_parser_rows_never_become_inventory_items_and_issues_are_retained(self) -> None:
        parsed = _parse(
            [
                _base_item(ingameEquippedId="stale-owner"),
                {"rank": "Epic"},
            ],
            heroes=[],
        )

        merged = merge_fribbels_inventory((), parsed)

        self.assertEqual(parsed.accepted_count, 1)
        self.assertEqual(parsed.rejected_count, 1)
        self.assertEqual(len(merged.items), 1)
        self.assertEqual(merged.source_warnings, parsed.warnings)
        self.assertEqual(merged.source_rejections, parsed.rejections)

    def test_existing_rows_absent_from_import_are_retained_and_reported_unseen(self) -> None:
        initial = merge_fribbels_inventory(
            (),
            _parse([_base_item(ingameId="one"), _base_item(ingameId="two")]),
        )
        second = merge_fribbels_inventory(
            initial.items,
            _parse([_base_item(ingameId="one")]),
        )

        self.assertEqual(len(second.items), 2)
        self.assertEqual(len(second.unchanged), 1)
        self.assertEqual(
            second.unseen_existing_ids,
            ("fribbels:item:ingame:two",),
        )

    def test_existing_inventory_invariants_are_validated_before_merging(self) -> None:
        initial = merge_fribbels_inventory((), _parse([_base_item(ingameId="one")]))
        with self.assertRaisesRegex(FribbelsMergeInputError, "stable item IDs"):
            merge_fribbels_inventory((initial.items[0], initial.items[0]), _parse([]))

        state = initial.items[0]
        wrong_gear = GearItem(
            item_id="wrong",
            slot=state.gear_item.slot,
            gear_set=state.gear_item.gear_set,
            main_stat=state.gear_item.main_stat,
            main_stat_value=state.gear_item.main_stat_value,
            substats=state.gear_item.substats,
            item_level=state.gear_item.item_level,
            enhance=state.gear_item.enhance,
        )
        with self.assertRaisesRegex(FribbelsMergeInputError, "must match"):
            replace(state, gear_item=wrong_gear)

    def test_result_and_inventory_metadata_are_deeply_immutable(self) -> None:
        result = merge_fribbels_inventory((), _parse([_base_item(ingameId="one")]))
        state = result.items[0]

        self.assertIsInstance(state.source_metadata, FrozenJsonObject)
        self.assertIsInstance(state.user_metadata, FrozenJsonObject)
        with self.assertRaises(FrozenInstanceError):
            result.unseen_existing_ids = ("changed",)  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            state.name = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            state.source_metadata["new"] = True  # type: ignore[index]

    def test_identity_merge_module_has_no_database_ui_logging_or_process_hash_dependency(self) -> None:
        source = inspect.getsource(merge_module)
        for forbidden in (
            "sqlite3",
            "user_data",
            "src.desktop",
            "src.ui",
            "logging",
        ):
            self.assertNotIn(forbidden, source)
        calls = {
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue({"print", "hash"}.isdisjoint(calls))


if __name__ == "__main__":
    unittest.main()
