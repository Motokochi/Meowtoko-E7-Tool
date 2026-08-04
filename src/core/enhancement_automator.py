import json
import threading
import time
from datetime import datetime
from pathlib import Path

from src.core.config_manager import DEBUG_DIR
from src.core.enhancement_rules import (
    ENHANCE_TARGETS,
    AutomationState,
    decide_enhancement_action,
    record_enhancement_event,
)
from src.core.live_packet_source import (
    EnhancementPacketTimeout,
    LivePacketSource,
)
from src.vision.automation_backend import AdbAutomationBackend


class AutomationStopped(Exception):
    """Raised internally when the user requests a clean stop."""


class EnhancementAutomator:
    """Enhance through ADB and make decisions from exact decoded packet events."""

    def __init__(
        self,
        settings,
        allow_destroy,
        max_pieces,
        on_log,
        on_complete,
        on_error,
        backend=None,
        *,
        cancel_check=None,
        on_progress=None,
        debug_dir=None,
        packet_source=None,
        item_metadata_resolver=None,
        enhancement_normalizer=None,
    ):
        self.settings = settings
        self.allow_destroy = allow_destroy
        self.max_pieces = max_pieces
        self.on_log = on_log
        self.on_complete = on_complete
        self.on_error = on_error
        self.backend = backend or AdbAutomationBackend(settings)
        self.packet_source = packet_source or LivePacketSource()
        self.item_metadata_resolver = item_metadata_resolver or (lambda _item_id: None)
        self.enhancement_normalizer = enhancement_normalizer
        self.cancel_check = cancel_check or (lambda: False)
        self.on_progress = on_progress or (lambda *_args: None)
        self.debug_dir = Path(debug_dir or DEBUG_DIR)
        self.stop_event = threading.Event()
        if hasattr(self.backend, "cancel_check"):
            self.backend.cancel_check = self._is_stopped
        self.thread = None
        self.completed_pieces = 0
        self.current_piece = 0
        self.last_decision = None
        self.last_debug_log = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def run(self):
        """Run synchronously; desktop controllers own the worker thread."""
        self.stop_event.clear()
        return self._run()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        try:
            self.packet_source.start()
            self._check_stopped()
            piece_number = 0
            while self._has_more_pieces(piece_number):
                self._check_stopped()
                piece_number += 1
                self.current_piece = piece_number
                self._progress("piece", f"Processing piece {piece_number}…", 0.0, piece_number)
                self._log(f"Processing piece {piece_number} via {self.backend.name}.")
                self._process_current_piece(piece_number)
                self.completed_pieces = piece_number
                self._progress("piece_complete", f"Finished piece {piece_number}.", 1.0, piece_number)
        except AutomationStopped:
            self._log("Automation stopped.")
            self.on_complete()
            return {
                "outcome": "cancelled" if self._is_stopped() else "stopped",
                "processed_pieces": self.completed_pieces,
                "current_piece": self.current_piece,
                "last_decision": self.last_decision,
            }
        except Exception as exc:
            self.on_error(str(exc))
            if self.thread is threading.current_thread():
                return None
            raise
        else:
            if self.max_pieces:
                self._log(f"Reached max pieces: {self.max_pieces}.")
            self.on_complete()
            return {
                "outcome": "completed",
                "processed_pieces": self.completed_pieces,
                "current_piece": self.current_piece,
                "last_decision": self.last_decision,
            }
        finally:
            self.packet_source.stop()

    def _has_more_pieces(self, piece_number):
        return not self._is_stopped() and (
            self.max_pieces is None or piece_number < self.max_pieces
        )

    def _process_current_piece(self, piece_number):
        state = AutomationState()
        packet = self._probe_and_wait(piece_number)
        item_id = packet.item_id
        metadata = self._resolve_item_metadata(item_id)
        gear_set = metadata["set"]
        initial_substats = metadata["initialSubstats"]
        imported_enhancement = metadata["enhance"]
        state.initial_substat_count = initial_substats
        state.archetype_context = {
            "setId": metadata["setId"],
            "slotId": metadata["slotId"],
            "mainStatId": metadata["mainStatId"],
        }
        if imported_enhancement >= 15:
            raise RuntimeError(
                f"Imported equipment {item_id} is already +15. "
                "Select gear below +15 and start again."
            )
        self._log(f"Packet matched equipment {item_id}{f' · {gear_set}' if gear_set else ''}.")

        normalized = self._normalize_packet(packet, initial_substats)
        observed_rolls = self._reconcile_probe_history(
            normalized,
            imported_enhancement,
        )
        decision = None
        parsed_data = None
        observed_checkpoint = observed_rolls * 3
        for enhancement in ENHANCE_TARGETS[:observed_rolls]:
            parsed_data = self._parsed_checkpoint(
                normalized,
                enhancement,
                require_exact_history=enhancement == observed_checkpoint,
            )
            record_enhancement_event(parsed_data, state)
            decision = decide_enhancement_action(parsed_data, state)

        if decision is None:
            first_target = self._next_checkpoint(imported_enhancement)
            if first_target is None:
                raise RuntimeError("The imported enhancement level could not be reconciled.")
            packet = self._enhance_and_wait(
                first_target,
                expected_item_id=item_id,
                piece_number=piece_number,
            )
            normalized = self._normalize_packet(packet, initial_substats)
            parsed_data = self._parsed_checkpoint(
                normalized,
                first_target,
                require_exact_history=True,
            )
            record_enhancement_event(parsed_data, state)
            decision = decide_enhancement_action(parsed_data, state)

        if decision is None or parsed_data is None:
            raise RuntimeError("The imported enhancement level could not be reconciled.")
        self._save_packet_debug(parsed_data, state, metadata)

        while not self._is_stopped():
            self.last_decision = self._decision_payload(decision)
            self._progress("decision", decision.reason, 0.58, piece_number, self.last_decision)
            self._log_decision(decision)

            if decision.action == "enhance":
                packet = self._enhance_and_wait(
                    decision.next_target,
                    expected_item_id=item_id,
                    piece_number=piece_number,
                )
                normalized = self._normalize_packet(packet, initial_substats)
                parsed_data = self._parsed_checkpoint(
                    normalized,
                    decision.next_target,
                    require_exact_history=True,
                )
                record_enhancement_event(parsed_data, state)
                self._save_packet_debug(parsed_data, state, metadata)
                decision = decide_enhancement_action(parsed_data, state)
                continue

            if decision.enhancement >= 15:
                self._dismiss_reward_popup()
            if decision.action == "destroy":
                if self.allow_destroy:
                    self._destroy_current_piece(piece_number)
                    return
                self._log("Destroy recommended, but destroy clicks are disabled. Automation stopped.")
                raise AutomationStopped()
            if decision.action == "lock":
                self._finish_locked_piece(piece_number)
                return
            self._log(f"Stopped: {decision.reason}")
            raise AutomationStopped()
        raise AutomationStopped()

    def _probe_and_wait(self, piece_number):
        self._log("Identifying the selected piece with one basic powder.")
        self._mark_packet_boundary()
        self._click("probe_ingredient")
        self._sleep("after_level_select_seconds", default=0.4)
        self._click("probe_select")
        self._sleep("after_level_select_seconds", default=0.4)
        self._click("enhance")
        return self._enhance_and_wait(
            None,
            expected_item_id=None,
            piece_number=piece_number,
            perform_clicks=False,
            label="identification",
        )

    def _enhance_and_wait(
        self,
        target,
        *,
        expected_item_id,
        piece_number,
        perform_clicks=True,
        label=None,
    ):
        attempts = self._enhancement_read_attempts()
        timeout = self._automation_float("enhancement_packet_timeout_seconds", 2.0)
        label = label or f"+{target}"
        if perform_clicks:
            self._mark_packet_boundary()
            self._enhance_to_target(target)
        enhanced_at = time.monotonic()
        for attempt in range(1, attempts + 1):
            self._check_stopped()
            self._progress(
                "packet",
                f"Waiting for the exact {label} enhancement packet…",
                0.36,
                piece_number,
            )
            try:
                packet = self.packet_source.wait_for_enhancement(
                    expected_item_id=expected_item_id,
                    timeout=timeout,
                    cancel_check=self._is_stopped,
                )
                remaining_animation = (
                    self._automation_float("after_enhance_seconds", 2.0)
                    - (time.monotonic() - enhanced_at)
                )
                if remaining_animation > 0:
                    self._progress(
                        "animation",
                        f"Enhancement confirmed; waiting for the {label} animation…",
                        0.42,
                        piece_number,
                    )
                    self._sleep_seconds(remaining_animation)
                return packet
            except EnhancementPacketTimeout as error:
                self._check_stopped()
                if attempt < attempts:
                    self._log(
                        f"No {label} packet arrived after {timeout:g}s; "
                        f"continuing to wait ({attempt}/{attempts - 1})."
                    )
                    self._sleep_seconds(
                        self._automation_float("after_enhancement_retry_seconds", 0.8)
                    )
                    continue
                raise RuntimeError(
                    f"Could not confirm {label} from packet traffic after {attempts} attempts. "
                    "Automation stopped before any lock or destroy click."
                ) from error
        raise RuntimeError("Enhancement packet retry loop ended unexpectedly.")

    @staticmethod
    def _reconcile_probe_history(normalized, imported_enhancement):
        try:
            observed_rolls = len(normalized["enhancementRollStats"])
        except (KeyError, TypeError) as error:
            raise RuntimeError(f"Enhancement packet history is invalid: {error}") from error
        imported_rolls = imported_enhancement // 3
        if observed_rolls < imported_rolls or observed_rolls > min(5, imported_rolls + 1):
            raise RuntimeError(
                f"The identification packet reports {observed_rolls} enhancement events, "
                f"but imported +{imported_enhancement} gear expects {imported_rolls}, "
                "with at most one new checkpoint from the identification powder. "
                "Import a fresh gear.txt and start again."
            )
        return observed_rolls

    def _resolve_item_metadata(self, item_id):
        try:
            metadata = self.item_metadata_resolver(item_id)
        except Exception as error:
            raise RuntimeError(f"Imported inventory lookup failed: {error}") from error
        if not isinstance(metadata, dict):
            raise RuntimeError(
                f"Equipment {item_id} was not found in imported inventory. "
                "Import a fresh gear.txt before running Enhancer."
            )
        gear_set = metadata.get("set")
        enhancement = metadata.get("enhance")
        initial_substats = metadata.get("initialSubstats")
        set_id = metadata.get("setId")
        slot_id = metadata.get("slotId")
        main_stat_id = metadata.get("mainStatId")
        if (
            not isinstance(gear_set, str)
            or not gear_set
            or isinstance(enhancement, bool)
            or not isinstance(enhancement, int)
            or not 0 <= enhancement <= 15
            or isinstance(initial_substats, bool)
            or not isinstance(initial_substats, int)
            or not 0 <= initial_substats <= 4
            or not isinstance(set_id, str)
            or not set_id
            or not isinstance(slot_id, str)
            or not slot_id
            or not isinstance(main_stat_id, str)
            or not main_stat_id
        ):
            raise RuntimeError(
                f"Imported metadata for equipment {item_id} is incomplete. "
                "Import a fresh gear.txt before running Enhancer."
            )
        return {
            "set": gear_set,
            "enhance": enhancement,
            "initialSubstats": initial_substats,
            "setId": set_id,
            "slotId": slot_id,
            "mainStatId": main_stat_id,
        }

    @staticmethod
    def _next_checkpoint(enhancement):
        return next((target for target in ENHANCE_TARGETS if target > enhancement), None)

    def _normalize_packet(self, packet, initial_substats):
        if self.enhancement_normalizer is None:
            raise RuntimeError("The private packet service is unavailable.")
        try:
            return self.enhancement_normalizer(packet, None, initial_substats)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Enhancement packet history is invalid: {error}") from error

    @staticmethod
    def _parsed_checkpoint(normalized, enhancement, *, require_exact_history):
        expected_rolls = enhancement // 3
        try:
            roll_stats = normalized["enhancementRollStats"]
            checkpoints = normalized["parsedCheckpoints"]
            actual_rolls = len(roll_stats)
            if require_exact_history and actual_rolls != expected_rolls:
                raise RuntimeError(
                    f"The packet reports {actual_rolls} enhancement events, but imported gear "
                    f"requires {expected_rolls} at +{enhancement}. Import a fresh gear.txt "
                    "and start again."
                )
            if len(checkpoints) < expected_rolls:
                raise RuntimeError(
                    f"The packet does not contain the +{enhancement} checkpoint."
                )
            return checkpoints[expected_rolls - 1]
        except (KeyError, TypeError, IndexError) as error:
            raise RuntimeError(f"Enhancement packet history is invalid: {error}") from error

    def _mark_packet_boundary(self):
        mark = getattr(self.packet_source, "mark_boundary", None)
        if callable(mark):
            mark()

    def _save_packet_debug(self, parsed_data, state, metadata):
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "backend": self.backend.name,
            "item_id": parsed_data["_enhancement_event"]["itemId"],
            "gear_set": metadata["set"],
            "imported_enhancement": metadata["enhance"],
            "initial_substat_count": metadata["initialSubstats"],
            "enhancement_roll_stats": list(state.roll_stats),
            "parsed_data": parsed_data,
        }
        path = self.debug_dir / "latest_enhancement_packet.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        self.last_debug_log = (
            f"Decoded +{parsed_data['enhance'].lstrip('+')} for equipment "
            f"{payload['item_id']} from packet traffic."
        )

    def _enhance_to_target(self, target):
        self._log(f"Enhancing toward +{target}.")
        self._click("auto_select")
        self._sleep("after_auto_select_seconds", default=0.6)
        self._click_level(target)
        self._sleep("after_level_select_seconds", default=0.4)
        self._click("enhance")

    def _click_level(self, target):
        key = f"+{target}"
        level_points = self.settings["click_points"]["levels"]
        if key not in level_points:
            raise RuntimeError(f"Missing click point for {key} Level.")
        self._check_stopped()
        self.backend.click_point(level_points[key])

    def _click(self, key):
        points = self.settings["click_points"]
        if key not in points:
            raise RuntimeError(f"Missing click point for {key}.")
        self._check_stopped()
        self.backend.click_point(points[key])

    def _dismiss_reward_popup(self):
        self._log("Final enhancement selected. Closing the reward popup.")
        self._click_above("lock", pixels=100)
        self._sleep("after_reward_popup_seconds", default=0.6)

    def _finish_locked_piece(self, piece_number):
        self._click("lock")
        self._sleep("after_lock_seconds", default=0.4)
        self._log("Lock clicked.")
        self._advance_after_lock(piece_number)

    def _destroy_current_piece(self, piece_number):
        self._click("destroy")
        self._sleep("after_destroy_seconds", default=0.6)
        self._click("destroy_confirm")
        self._sleep("after_destroy_confirm_seconds", default=1.0)
        self._log("Destroy and confirmation clicked.")
        self._advance_after_destroy(piece_number)

    def _advance_after_lock(self, piece_number):
        if not self._has_more_pieces(piece_number):
            return
        self._log("Returning to inventory and opening the next piece.")
        self._click("back")
        self._sleep("after_back_seconds", default=0.8)
        self._open_next_piece()

    def _advance_after_destroy(self, piece_number):
        if not self._has_more_pieces(piece_number):
            return
        self._log("Opening the next piece after destroy.")
        self._open_next_piece()

    def _open_next_piece(self):
        self._click("next_piece")
        self._sleep("after_next_piece_seconds", default=0.6)
        self._click("open_enhance")
        self._sleep("after_open_enhance_seconds", default=0.8)

    def _click_above(self, key, pixels):
        points = self.settings["click_points"]
        if key not in points:
            raise RuntimeError(f"Missing click point for {key}.")
        point = {
            "x": points[key]["x"],
            "y": max(0, int(points[key]["y"]) - int(pixels)),
        }
        self._check_stopped()
        self.backend.click_point(point)

    def _sleep(self, key, default):
        self._sleep_seconds(self._automation_float(key, default))

    def _sleep_seconds(self, delay):
        end_time = time.time() + float(delay)
        while time.time() < end_time:
            self._check_stopped()
            time.sleep(0.1)

    def _automation_float(self, key, default):
        return float(self.settings.get("automation", {}).get(key, default))

    def _enhancement_read_attempts(self):
        retries = int(self._automation_float("enhancement_read_retries", 2))
        return max(1, retries + 1)

    def _check_stopped(self):
        if self._is_stopped():
            raise AutomationStopped()

    def _is_stopped(self):
        return self.stop_event.is_set() or bool(self.cancel_check())

    def _progress(self, stage, message, local_progress, piece_number, last_decision=None):
        if self.max_pieces:
            progress = ((max(1, piece_number) - 1) + float(local_progress)) / self.max_pieces
        else:
            piece = max(1, piece_number)
            base = 1.0 - (1.0 / piece)
            span = (1.0 / piece) - (1.0 / (piece + 1))
            progress = base + float(local_progress) * span
        self.on_progress(
            stage,
            message,
            max(0.0, min(0.99, progress)),
            piece_number,
            last_decision,
        )

    @staticmethod
    def _decision_payload(decision):
        return {
            "action": decision.action,
            "reason": decision.reason,
            "currentGs": decision.current_gs,
            "potentialGs": decision.potential_gs,
            "enhancement": decision.enhancement,
            "nextTarget": decision.next_target,
        }

    def _log_decision(self, decision):
        self._log(
            f"+{decision.enhancement} | Current GS {decision.current_gs:.1f} | "
            f"Potential GS {decision.potential_gs:.1f} | "
            f"{decision.action.upper()} | {decision.reason}"
        )

    def _log(self, message):
        self.on_log(message)
