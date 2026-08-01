from __future__ import annotations

import json
import tempfile
import time
import threading
import unittest
from pathlib import Path

from src.desktop.optimizer_inventory_service import OptimizerInventoryService
from src.desktop.optimizer_profile_service import OptimizerProfileService
from src.desktop.optimizer_result_controller import OptimizerResultController
from src.desktop.optimizer_result_controller import (
    OptimizerResultDetailUnavailableError,
    OptimizerResultEquipUnavailableError,
)
from src.desktop.optimizer_result_service import OptimizerResultService, result_options
from src.desktop.optimizer_search_service import OptimizerSearchService
from src.desktop.protocol import dispatch_message
from src.desktop import PROTOCOL_VERSION
from src.optimizer.cuda.runtime import diagnose_cuda_runtime
from src.optimizer.data import InventoryRepository
from src.optimizer.domain import GEAR_SLOT_ORDER, GearSet, ItemStatType, item_stat_fribbels_name
from src.optimizer.result_store import RESULT_DERIVED_METRIC_ORDER, RESULT_PRIMARY_STAT_ORDER
from tests.test_cpu_orchestration import _gear_row
from tests.test_optimizer_search_backend_integration import BackendSession, _write_inventory


def _empty_range() -> dict[str, str | None]:
    return {"minimum": None, "maximum": None}


def _query(run_id: str, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "runId": run_id,
        "category": "all",
        "sortKey": "priority-score",
        "direction": "descending",
        "pageIndex": 0,
        "pageSize": 100,
        "primaryRanges": {item["fieldId"]: _empty_range() for item in result_options()["primaryFields"]},
        "derivedRanges": {item: _empty_range() for item in RESULT_DERIVED_METRIC_ORDER},
        "priorityScore": _empty_range(),
        "constraintDistance": _empty_range(),
        "replacementCount": _empty_range(),
        "equippedCount": _empty_range(),
    }
    value.update(changes)
    return value


def _wait(controller: OptimizerResultController, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = controller.get_snapshot()
        if snapshot["state"] != "running":
            return snapshot
        time.sleep(0.005)
    raise AssertionError(controller.get_snapshot())


def _wait_detail(controller: OptimizerResultController, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = controller.get_detail_snapshot()
        if snapshot["state"] != "loading":
            return snapshot
        time.sleep(0.005)
    raise AssertionError(controller.get_detail_snapshot())


def _wait_export(controller: OptimizerResultController, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = controller.get_export_snapshot()
        if snapshot["state"] != "running":
            return snapshot
        time.sleep(0.005)
    raise AssertionError(controller.get_export_snapshot())


class _CompletedSearch:
    def __init__(self, run_id: str, prepared: object, counts: tuple[int, int, int]) -> None:
        self.completed = (run_id, prepared)
        self.counts = counts
        self.invalidator = lambda: None

    def set_result_invalidator(self, invalidator):
        self.invalidator = invalidator

    def get_completed_context(self, run_id=None):
        return self.completed if run_id in (None, self.completed[0]) else None

    def get_snapshot(self):
        return {
            "categoryCounts": {
                "exact": str(self.counts[0]),
                "oneAway": str(self.counts[1]),
                "twoAway": str(self.counts[2]),
            }
        }

    def reset_for_data_erasure(self):
        self.completed = None
        self.invalidator()
        return {"state": "idle"}


class OptimizerResultDesktopTests(unittest.TestCase):
    def _fixture(
        self,
        maximum_distance: int = 0,
        category: str = "exact",
        equipped_owners: bool = False,
        overflowing_crit: bool = False,
    ):
        temporary = tempfile.TemporaryDirectory(prefix="e7-result-desktop-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sets = {
            "exact": (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            "one-away": (GearSet.SPEED,) * 3 + (GearSet.HEALTH,) * 2 + (GearSet.ATTACK,),
            "two-away": (GearSet.SPEED,) * 2 + (GearSet.HEALTH,) * 2 + (GearSet.ATTACK,) * 2,
        }[category]
        rows = [
            _gear_row(
                f"private-result-{index}",
                slot,
                sets[index],
            )
            for index, slot in enumerate(GEAR_SLOT_ORDER)
        ]
        if overflowing_crit:
            for row in rows:
                row["substats"] = [{
                    "type": item_stat_fribbels_name(ItemStatType.CRITICAL_HIT_CHANCE_PERCENT),
                    "value": 20,
                    "reforgedValue": 20,
                }]
        heroes = []
        if equipped_owners:
            rows[0]["ingameEquippedId"] = "inventory-hero-ras"
            rows[1]["ingameEquippedId"] = "inventory-hero-alencia"
            heroes = [
                {"id": "inventory-hero-ras", "name": "Ras"},
                {"id": "inventory-hero-alencia", "name": "Alencia"},
            ]
        source = root / "private" / "gear.txt"
        source.parent.mkdir()
        source.write_text(json.dumps({"items": rows, "heroes": heroes}), encoding="utf-8")
        OptimizerInventoryService(root).import_file(source)
        profiles = OptimizerProfileService(root)
        hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
        draft = profiles.load_draft(hero_id)["draft"]
        draft["maximumReplacementDistance"] = maximum_distance
        draft["includeEquipped"] = equipped_owners
        search = OptimizerSearchService(
            root,
            profile_service=profiles,
            cuda_diagnostic=lambda: diagnose_cuda_runtime(disabled=True),
            cpu_batch_size=1,
            result_write_batch_size=1,
        )
        prepared = search.prepare(draft, "request.result-explorer", lambda: False)
        run_id = "run-result-explorer"
        execution = search.run(prepared, run_id, lambda: False, lambda *_args: None)
        owner = _CompletedSearch(run_id, prepared, execution.category_counts)
        events = []
        controller = OptimizerResultController(
            OptimizerResultService(root, search.result_store),
            owner,  # type: ignore[arg-type]
            events.append,
            event_interval_seconds=0,
        )
        self.addCleanup(controller.close)
        return root, run_id, controller, events

    def _completed_detail(self, category: str) -> dict:
        _root, run_id, controller, _events = self._fixture(category=category)
        controller.query(_query(run_id))
        page = _wait(controller)
        self.assertEqual("completed", page["state"])
        self.assertEqual(category, page["rows"][0]["category"])
        started = controller.detail({
            "runId": run_id,
            "queryId": page["queryId"],
            "rowKey": page["rows"][0]["rowKey"],
        })
        self.assertEqual("loading", started["state"])
        detail = _wait_detail(controller)
        self.assertEqual("completed", detail["state"], detail)
        return detail["detail"]

    def test_repository_equipment_assignment_replaces_the_old_local_build_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-equip-repository-") as directory:
            root = Path(directory)
            rows = [
                _gear_row(f"selected-{index}", slot, GearSet.SPEED)
                for index, slot in enumerate(GEAR_SLOT_ORDER)
            ]
            rows[0]["ingameEquippedId"] = "hero-ras"
            rows[1]["ingameEquippedId"] = "hero-alencia"
            previous_weapon = _gear_row("previous-weapon", GEAR_SLOT_ORDER[0], GearSet.HEALTH)
            previous_weapon["ingameEquippedId"] = "hero-ras"
            source = root / "gear.txt"
            source.write_text(json.dumps({
                "items": [*rows, previous_weapon],
                "heroes": [
                    {"id": "hero-ras", "name": "Ras"},
                    {"id": "hero-alencia", "name": "Alencia"},
                ],
            }), encoding="utf-8")
            OptimizerInventoryService(root).import_file(source)
            repository = InventoryRepository(root / "optimizer.db")
            selected_ids = tuple(
                item.stable_item_id
                for item in repository.load_inventory()
                if item.current_ingame_id and item.current_ingame_id.startswith("selected-")
            )

            outcome = repository.assign_equipment_build("hero-ras", "Ras", selected_ids)

            self.assertEqual(6, outcome.assigned_items)
            self.assertEqual(1, outcome.already_on_target)
            self.assertEqual(1, outcome.moved_from_other_heroes)
            self.assertEqual(4, outcome.newly_equipped_items)
            self.assertEqual(1, outcome.unequipped_from_target)
            self.assertEqual(6, outcome.total_equipped_items)
            state = repository.load_inventory()
            self.assertIsNone(next(
                item for item in state if item.current_ingame_id == "previous-weapon"
            ).gear_item.equipped_hero_id)
            self.assertTrue(all(
                item.gear_item.equipped_hero_id == "hero-ras"
                for item in state if item.stable_item_id in selected_ids
            ))
            before_invalid = repository.dense_snapshot()
            with self.assertRaisesRegex(ValueError, "six unique"):
                repository.assign_equipment_build("hero-ras", "Ras", selected_ids[:-1])
            self.assertEqual(before_invalid, repository.dense_snapshot())

    def test_options_cover_every_sort_and_page_dto_is_bounded_and_private(self) -> None:
        options = result_options()
        self.assertEqual(8, len(options["primaryFields"]))
        self.assertEqual(15, len(options["derivedFields"]))
        self.assertEqual(25, len(options["sortOptions"]))
        self.assertEqual(1000, options["maxPageSize"])
        self.assertEqual("attack", options["primaryFields"][0]["fieldId"])
        self.assertEqual("primary:final_stat.attack", options["primaryFields"][0]["sortKey"])

        _root, run_id, controller, events = self._fixture()
        started = controller.query(_query(run_id))
        self.assertEqual("running", started["state"])
        result = _wait(controller)
        self.assertEqual("completed", result["state"])
        self.assertEqual("1", result["filteredRows"])
        self.assertEqual(1, len(result["rows"]))
        row = result["rows"][0]
        self.assertEqual(8, len(row["primaryStats"]))
        self.assertEqual(15, len(row["derivedMetrics"]))
        self.assertEqual("exact", row["category"])
        encoded = json.dumps({"result": result, "events": events})
        for private in ("private-result", "gear.txt", "cacheKey", "rowOrdinal", "denseItem"):
            self.assertNotIn(private, encoded)

    def test_retired_near_category_is_rejected(self) -> None:
        _root, run_id, controller, _events = self._fixture(maximum_distance=0)
        controller.query(_query(run_id, category="two-away"))
        result = _wait(controller)
        self.assertEqual("failed", result["state"])
        self.assertEqual("invalid-query", result["failure"]["code"])
        self.assertEqual([], result["rows"])

    def test_inventory_replacement_invalidates_resolution_context(self) -> None:
        root, run_id, controller, _events = self._fixture()
        new_row = _gear_row("new-private-piece", GEAR_SLOT_ORDER[0], GearSet.ATTACK)
        source = root / "replacement.txt"
        source.write_text(json.dumps({"items": [new_row]}), encoding="utf-8")
        OptimizerInventoryService(root).import_file(source)
        controller.query(_query(run_id))
        result = _wait(controller)
        self.assertEqual("failed", result["state"])
        self.assertEqual("inventory-changed", result["failure"]["code"])
        self.assertEqual([], result["rows"])

    def test_exact_detail_has_six_owned_items_and_no_future_replacements(self) -> None:
        detail = self._completed_detail("exact")
        self.assertEqual("exact", detail["category"])
        self.assertEqual("set-complete", detail["guidance"]["kind"])
        self.assertEqual(6, len(detail["gear"]))
        self.assertEqual(
            [slot.value for slot in GEAR_SLOT_ORDER],
            [item["slotId"] for item in detail["gear"]],
        )
        self.assertEqual(8, len(detail["constraints"]["primary"]))
        self.assertEqual(15, len(detail["constraints"]["derived"]))
        self.assertTrue(all(item["rankLabel"] == "Epic" for item in detail["gear"]))
        self.assertTrue(all(item["equippedHeroName"] is None for item in detail["gear"]))

    def test_results_display_filter_and_detail_use_raw_overflowing_critical_hit_chance(self) -> None:
        _root, run_id, controller, _events = self._fixture(overflowing_crit=True)
        query = _query(run_id)
        query["primaryRanges"]["criticalHitChancePercent"] = {
            "minimum": "120",
            "maximum": None,
        }
        controller.query(query)
        page = _wait(controller)
        self.assertEqual("completed", page["state"], page)
        self.assertEqual(1, len(page["rows"]))
        displayed = int(page["rows"][0]["primaryStats"]["criticalHitChancePercent"])
        self.assertGreater(displayed, 100)
        controller.detail({
            "runId": run_id,
            "queryId": page["queryId"],
            "rowKey": page["rows"][0]["rowKey"],
        })
        detail = _wait_detail(controller)
        self.assertEqual(str(displayed), detail["detail"]["primaryStats"]["criticalHitChancePercent"])

    def test_detail_resolves_equipped_owner_names_and_selected_hero_aliases(self) -> None:
        _root, run_id, controller, _events = self._fixture(equipped_owners=True)
        controller.query(_query(run_id))
        page = _wait(controller)
        controller.detail({
            "runId": run_id,
            "queryId": page["queryId"],
            "rowKey": page["rows"][0]["rowKey"],
        })
        detail = _wait_detail(controller)["detail"]

        self.assertEqual("selected-hero", detail["gear"][0]["equippedStatus"])
        self.assertEqual("Ras", detail["gear"][0]["equippedHeroName"])
        self.assertEqual("other-hero", detail["gear"][1]["equippedStatus"])
        self.assertEqual("Alencia", detail["gear"][1]["equippedHeroName"])
        self.assertTrue(all(
            item["equippedHeroName"] is None
            for item in detail["gear"][2:]
        ))

    def test_detail_rejects_forged_or_stale_visible_selection(self) -> None:
        _root, run_id, controller, _events = self._fixture()
        controller.query(_query(run_id))
        page = _wait(controller)
        with self.assertRaises(OptimizerResultDetailUnavailableError):
            controller.detail({"runId": run_id, "queryId": page["queryId"], "rowKey": "forged"})
        old_row = page["rows"][0]["rowKey"]
        old_query = page["queryId"]
        controller.query(_query(run_id, direction="ascending"))
        with self.assertRaises(OptimizerResultDetailUnavailableError):
            controller.detail({"runId": run_id, "queryId": old_query, "rowKey": old_row})

    def test_equip_assigns_the_visible_six_piece_build_locally_and_retains_results(self) -> None:
        root, run_id, controller, _events = self._fixture(equipped_owners=True)
        controller.query(_query(run_id))
        page = _wait(controller)
        controller.detail({
            "runId": run_id,
            "queryId": page["queryId"],
            "rowKey": page["rows"][0]["rowKey"],
        })
        _wait_detail(controller)
        result = controller.equip({
            "runId": run_id,
            "queryId": page["queryId"],
            "rowKey": page["rows"][0]["rowKey"],
        })

        self.assertEqual("equipped", result["state"])
        self.assertEqual("Ras", result["heroName"])
        self.assertEqual(6, result["equippedCount"])
        self.assertEqual(1, result["alreadyEquipped"])
        self.assertEqual(1, result["movedFromOtherHeroes"])
        self.assertEqual(4, result["newlyEquipped"])
        self.assertEqual(0, result["unequippedFromHero"])
        self.assertEqual(6, result["inventoryEquippedItems"])
        equipped = [
            item for item in InventoryRepository(root / "optimizer.db").load_inventory()
            if item.gear_item.equipped_hero_id is not None
        ]
        self.assertEqual(6, len(equipped))
        self.assertTrue(all(item.gear_item.equipped_hero_id == "inventory-hero-ras" for item in equipped))
        self.assertTrue(all(item.equipped_by_name == "Ras" for item in equipped))
        self.assertEqual("completed", controller.get_snapshot()["state"])
        self.assertEqual(page["queryId"], controller.get_snapshot()["queryId"])
        self.assertEqual("completed", controller.get_detail_snapshot()["state"])
        self.assertEqual(page["rows"][0]["rowKey"], controller.get_detail_snapshot()["rowKey"])

        controller.query(_query(run_id, direction="ascending"))
        refreshed_page = _wait(controller)
        self.assertEqual("completed", refreshed_page["state"])
        controller.detail({
            "runId": run_id,
            "queryId": refreshed_page["queryId"],
            "rowKey": refreshed_page["rows"][0]["rowKey"],
        })
        refreshed_detail = _wait_detail(controller)["detail"]
        self.assertEqual(6, refreshed_detail["equippedCount"])
        self.assertTrue(all(
            item["equippedStatus"] == "selected-hero"
            for item in refreshed_detail["gear"]
        ))

    def test_equip_requires_selected_character_in_import_and_preserves_inventory_on_failure(self) -> None:
        root, run_id, controller, _events = self._fixture()
        controller.query(_query(run_id))
        page = _wait(controller)
        before = InventoryRepository(root / "optimizer.db").dense_snapshot()
        with self.assertRaisesRegex(
            OptimizerResultEquipUnavailableError,
            "not uniquely present",
        ):
            controller.equip({
                "runId": run_id,
                "queryId": page["queryId"],
                "rowKey": page["rows"][0]["rowKey"],
            })
        self.assertEqual(before, InventoryRepository(root / "optimizer.db").dense_snapshot())
        self.assertEqual("completed", controller.get_snapshot()["state"])

    def test_latest_detail_selection_wins_when_an_older_resolution_finishes_late(self) -> None:
        _root, run_id, controller, _events = self._fixture()
        controller.query(_query(run_id))
        page = _wait(controller)
        request = {"runId": run_id, "queryId": page["queryId"], "rowKey": page["rows"][0]["rowKey"]}
        original = controller.service.resolve_detail
        first_started = threading.Event()
        release_first = threading.Event()
        call_count = 0

        def delayed(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                self.assertTrue(release_first.wait(2))
            return original(*args)

        controller.service.resolve_detail = delayed  # type: ignore[method-assign]
        first = controller.detail(request)
        self.assertTrue(first_started.wait(1))
        second = controller.detail(request)
        release_first.set()
        completed = _wait_detail(controller)
        self.assertNotEqual(first["selectionId"], second["selectionId"])
        self.assertEqual(second["selectionId"], completed["selectionId"])
        self.assertEqual("completed", completed["state"])

    def test_protocol_accepts_only_bounded_result_operations(self) -> None:
        class FakeController:
            def get_options(self): return result_options()
            def get_snapshot(self): return {"state": "idle"}
            def query(self, query): return {"state": "running", "runId": query["runId"]}
            def cancel(self, query_id): return {"state": "cancelled", "queryId": query_id}
            def detail(self, selection): return {"state": "loading", **selection}
            def equip(self, selection): return {"state": "equipped", **selection}
            def get_export_snapshot(self): return {"state": "idle"}
            def start_export(self, request): return {"state": "running", "format": request["format"]}
            def cancel_export(self, export_id): return {"state": "cancelled", "exportId": export_id}

        def request(method: str, params: dict | None = None):
            return dispatch_message(
                {"protocol": PROTOCOL_VERSION, "id": "result-request", "method": method, "params": params or {}},
                optimizer_result_controller=FakeController(),
            )

        self.assertTrue(request("optimizer.results.options")["ok"])
        self.assertTrue(request("optimizer.results.get")["ok"])
        self.assertTrue(request("optimizer.results.query", {"query": {"runId": "run"}})["ok"])
        self.assertTrue(request("optimizer.results.cancel", {"queryId": "query"})["ok"])
        self.assertTrue(request("optimizer.results.detail", {"runId": "run", "queryId": "query", "rowKey": "row"})["ok"])
        self.assertTrue(request("optimizer.results.equip", {"runId": "run", "queryId": "query", "rowKey": "row"})["ok"])
        self.assertTrue(request("optimizer.results.export.get")["ok"])
        self.assertTrue(request("optimizer.results.export.start", {
            "runId": "run", "queryId": "query", "format": "csv", "destination": "private",
        })["ok"])
        self.assertTrue(request("optimizer.results.export.cancel", {"exportId": "export"})["ok"])
        self.assertEqual("invalid_params", request("optimizer.results.get", {"path": "private"})["error"]["code"])
        self.assertEqual("invalid_params", request("optimizer.results.query", {"query": {}, "rows": []})["error"]["code"])
        self.assertEqual("invalid_params", request("optimizer.results.detail", {"runId": "run", "rowOrdinal": 0})["error"]["code"])
        self.assertEqual("invalid_params", request("optimizer.results.equip", {"runId": "run", "queryId": "query", "rowOrdinal": 0})["error"]["code"])
        self.assertEqual("invalid_params", request("optimizer.results.export.start", {
            "runId": "run", "queryId": "query", "format": "csv", "destination": "private", "rows": [],
        })["error"]["code"])

    def test_active_filtered_view_exports_atomically_without_returning_destination(self) -> None:
        root, run_id, controller, _events = self._fixture()
        controller.query(_query(run_id))
        page = _wait(controller)
        destination = root / "private-export.csv"
        started = controller.start_export({
            "runId": run_id,
            "queryId": page["queryId"],
            "format": "csv",
            "destination": str(destination),
        })
        self.assertEqual("running", started["state"])
        completed = _wait_export(controller)
        self.assertEqual("completed", completed["state"], completed)
        self.assertEqual("1", completed["rowCount"])
        self.assertEqual("1", completed["writtenRows"])
        self.assertRegex(completed["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(destination.is_file())
        self.assertIn("rowOrdinal", destination.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn(str(destination), json.dumps(completed))

    def test_backend_process_serves_one_bounded_page_after_a_real_search(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-backend-") as directory:
            root = Path(directory)
            user_data = root / "user-data"
            source = root / "private-player" / "gear.txt"
            source.parent.mkdir()
            _write_inventory(source)
            backend = BackendSession(user_data)
            try:
                self.assertTrue(backend.request("import", "optimizer.inventory.import", {"sourcePath": str(source)})["ok"])
                hero = backend.request("hero", "optimizer.hero.search", {"query": "Ras", "limit": 1})["result"]["results"][0]
                draft = backend.request("draft", "optimizer.profile.load", {"heroId": hero["heroId"]})["result"]["draft"]
                backend.request("search", "optimizer.search.start", {"draft": draft})
                terminal = backend.wait_for_search()
                run_id = terminal["result"]["resultRunId"]
                options = backend.request("result-options", "optimizer.results.options")["result"]
                started = backend.request("result-query", "optimizer.results.query", {"query": _query(run_id)})
                result = started["result"]
                attempt = 0
                while result["state"] == "running" and attempt < 100:
                    result = backend.request(f"result-get-{attempt}", "optimizer.results.get")["result"]
                    attempt += 1
                    time.sleep(0.005)
                row = result["rows"][0]
                detail_started = backend.request(
                    "result-detail",
                    "optimizer.results.detail",
                    {"runId": run_id, "queryId": result["queryId"], "rowKey": row["rowKey"]},
                )["result"]
                detail_event = None
                for detail_attempt in range(100):
                    backend.request(f"detail-pump-{detail_attempt}", "optimizer.results.get")
                    matches = [
                        event["payload"] for event in backend.events
                        if event.get("event") == "optimizer.results.detail-updated"
                        and event.get("payload", {}).get("state") == "completed"
                    ]
                    if matches:
                        detail_event = matches[-1]
                        break
                    time.sleep(0.005)
                export_path = root / "bounded-results.csv"
                export_started = backend.request(
                    "result-export",
                    "optimizer.results.export.start",
                    {
                        "runId": run_id,
                        "queryId": result["queryId"],
                        "format": "csv",
                        "destination": str(export_path),
                    },
                )["result"]
                export_result = export_started
                for export_attempt in range(100):
                    if export_result["state"] != "running":
                        break
                    export_result = backend.request(
                        f"export-get-{export_attempt}", "optimizer.results.export.get"
                    )["result"]
                    time.sleep(0.005)
                equipped = backend.request(
                    "result-equip",
                    "optimizer.results.equip",
                    {"runId": run_id, "queryId": result["queryId"], "rowKey": row["rowKey"]},
                )
                inventory_after_equip = backend.request(
                    "inventory-after-equip",
                    "optimizer.inventory.get",
                )["result"]
                search_after_equip = backend.request(
                    "search-after-equip",
                    "optimizer.search.get",
                )["result"]
            finally:
                stderr = backend.stop()

            self.assertEqual(25, len(options["sortOptions"]))
            self.assertEqual("completed", result["state"])
            self.assertEqual(1, len(result["rows"]))
            self.assertEqual("loading", detail_started["state"])
            self.assertIsNotNone(detail_event)
            self.assertEqual(6, len(detail_event["detail"]["gear"]))
            self.assertEqual("set-complete", detail_event["detail"]["guidance"]["kind"])
            self.assertEqual("completed", export_result["state"], export_result)
            self.assertTrue(export_path.is_file())
            self.assertNotIn(str(export_path), json.dumps(export_result))
            self.assertTrue(equipped["ok"], equipped)
            self.assertEqual("equipped", equipped["result"]["state"])
            self.assertEqual("Ras", equipped["result"]["heroName"])
            self.assertEqual(6, equipped["result"]["equippedCount"])
            self.assertEqual(6, inventory_after_equip["equippedItems"])
            self.assertEqual("completed", search_after_equip["state"])
            self.assertTrue(any(event.get("event") == "optimizer.results.updated" for event in backend.events))
            self.assertTrue(any(event.get("event") == "optimizer.results.export-updated" for event in backend.events))
            public = json.dumps({
                "options": options,
                "result": result,
                "detail": detail_event,
                "equipped": equipped,
                "inventory": inventory_after_equip,
                "events": backend.events,
            }) + stderr
            self.assertNotIn(str(source), public)
            self.assertNotIn("private-player", public)
            self.assertNotIn("private-result-item", public)
            self.assertNotIn("cacheKey", public)
            self.assertNotIn("rowOrdinal", public)

            restarted = BackendSession(user_data)
            try:
                stale = restarted.request(
                    "stale-detail",
                    "optimizer.results.detail",
                    {"runId": run_id, "queryId": result["queryId"], "rowKey": row["rowKey"]},
                )
            finally:
                restarted.stop()
            self.assertFalse(stale["ok"])
            self.assertEqual("optimizer_result_detail_unavailable", stale["error"]["code"])

    def test_latest_query_wins_and_search_invalidation_cancels_stale_work(self) -> None:
        class SearchOwner(_CompletedSearch):
            invalidator = lambda self: None
            def set_result_invalidator(self, callback): self.invalidator = callback

        class BlockingResultService:
            def __init__(self):
                self.started = threading.Event()
            def execute(self, _prepared, _run_id, _query_id, payload, should_cancel, on_progress):
                self.started.set()
                while payload["pageIndex"] == 0 and not should_cancel():
                    time.sleep(0.002)
                if should_cancel():
                    from src.desktop.optimizer_result_service import OptimizerResultCancelled
                    raise OptimizerResultCancelled()
                on_progress("sorting", 1, 1)
                return {
                    "kind": "page", "filteredRows": "0", "pageIndex": 1, "pageSize": 100,
                    "pageCount": 0, "startOffset": "0", "endOffset": "0",
                    "hasPrevious": False, "hasNext": False, "outOfRange": True, "rows": [],
                }

        owner = SearchOwner("run-latest", object(), (1, 0, 0))
        service = BlockingResultService()
        identifiers = iter(("query-old", "query-new", "query-invalidated"))
        events = []
        controller = OptimizerResultController(
            service,  # type: ignore[arg-type]
            owner,  # type: ignore[arg-type]
            events.append,
            query_id_factory=lambda: next(identifiers),
            event_interval_seconds=0,
        )
        self.addCleanup(controller.close)
        controller.query(_query("run-latest", pageIndex=0))
        self.assertTrue(service.started.wait(1))
        controller.query(_query("run-latest", pageIndex=1))
        latest = _wait(controller)
        self.assertEqual("query-new", latest["queryId"])
        self.assertEqual("completed", latest["state"])
        self.assertFalse(any(event["queryId"] == "query-old" and event["state"] != "running" for event in events))

        service.started.clear()
        controller.query(_query("run-latest", pageIndex=0))
        self.assertTrue(service.started.wait(1))
        owner.invalidator()
        self.assertEqual("idle", controller.get_snapshot()["state"])
        time.sleep(0.02)
        self.assertEqual("idle", controller.get_snapshot()["state"])


if __name__ == "__main__":
    unittest.main()
