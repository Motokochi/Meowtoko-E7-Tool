"""Validated Epic Seven enhancement events decoded from packet messages."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


STAT_NAMES = {
    "att_rate": "Attack",
    "max_hp_rate": "Health",
    "def_rate": "Defense",
    "att": "Flat Attack",
    "max_hp": "Flat Health",
    "def": "Flat Defense",
    "speed": "Speed",
    "res": "Effect Resistance",
    "cri": "Critical Hit Chance",
    "cri_dmg": "Critical Hit Damage",
    "acc": "Effectiveness",
    "coop": "Dual Attack Chance",
}
PERCENT_STATS = frozenset(
    {"att_rate", "max_hp_rate", "def_rate", "res", "cri", "cri_dmg", "acc", "coop"}
)


class EnhancementPacketError(ValueError):
    pass


@dataclass(frozen=True)
class EnhancementOperation:
    stat_code: str
    amount: int | float


@dataclass(frozen=True)
class EnhancementPacket:
    item_id: str
    operations: tuple[EnhancementOperation, ...]

    @classmethod
    def from_message(cls, message: Mapping[str, Any]) -> "EnhancementPacket":
        item_id = message.get("equip")
        raw_operations = message.get("op")
        if isinstance(item_id, bool) or not isinstance(item_id, (str, int)) or not str(item_id).strip():
            raise EnhancementPacketError("Enhancement packet is missing its equipment ID.")
        if not isinstance(raw_operations, Sequence) or isinstance(raw_operations, (str, bytes)):
            raise EnhancementPacketError("Enhancement packet is missing its operation list.")

        operations: list[EnhancementOperation] = []
        for index, raw in enumerate(raw_operations):
            if (
                not isinstance(raw, Sequence)
                or isinstance(raw, (str, bytes))
                or len(raw) < 2
                or not isinstance(raw[0], str)
                or isinstance(raw[1], bool)
                or not isinstance(raw[1], (int, float))
            ):
                raise EnhancementPacketError(f"Enhancement operation {index} is invalid.")
            operations.append(EnhancementOperation(raw[0], raw[1]))
        if len(operations) < 2:
            raise EnhancementPacketError("Enhancement packet has no substat operation.")
        return cls(str(item_id).strip(), tuple(operations))

    @property
    def latest_roll_stat(self) -> str:
        """The response's final operation is the newest +3 enhancement event."""
        return self.operations[-1].stat_code

    def enhancement_rolls(
        self,
        initial_substat_count: int,
    ) -> tuple[EnhancementOperation, ...]:
        if (
            isinstance(initial_substat_count, bool)
            or not isinstance(initial_substat_count, int)
            or not 0 <= initial_substat_count <= 4
        ):
            raise EnhancementPacketError("Initial substat count must be between zero and four.")
        boundary = 1 + initial_substat_count
        if len(self.operations) < boundary:
            raise EnhancementPacketError(
                "Enhancement packet has fewer operations than the imported gear rarity requires."
            )
        return self.operations[boundary:]

    def parsed_gear_at(
        self,
        enhancement: int,
        initial_substat_count: int,
    ) -> dict[str, Any]:
        if enhancement not in {3, 6, 9, 12, 15}:
            raise EnhancementPacketError("Enhancement must be a +3 checkpoint.")
        roll_count = enhancement // 3
        rolls = self.enhancement_rolls(initial_substat_count)
        if len(rolls) < roll_count:
            raise EnhancementPacketError(
                f"Enhancement packet contains {len(rolls)} roll events, "
                f"but +{enhancement} requires {roll_count}."
            )
        included = (
            self.operations[1 : 1 + initial_substat_count]
            + rolls[:roll_count]
        )
        return self._parsed_gear(
            enhancement,
            included,
            rolls[roll_count - 1].stat_code,
        )

    def parsed_gear(self, enhancement: int) -> dict[str, Any]:
        return self._parsed_gear(
            enhancement,
            self.operations[1:],
            self.latest_roll_stat,
        )

    def _parsed_gear(
        self,
        enhancement: int,
        operations: Sequence[EnhancementOperation],
        latest_roll_stat: str,
    ) -> dict[str, Any]:
        totals: OrderedDict[str, int | float] = OrderedDict()
        for operation in operations:
            totals.setdefault(operation.stat_code, 0)
            totals[operation.stat_code] += operation.amount
        return {
            "enhance": f"+{enhancement}",
            "subs": [
                {
                    "stat": STAT_NAMES.get(code, code),
                    "val": _display_value(code, amount),
                }
                for code, amount in totals.items()
            ],
            "_enhancement_event": {
                "itemId": self.item_id,
                "statCode": latest_roll_stat,
            },
        }


def _display_value(code: str, amount: int | float) -> str:
    value = float(amount) * 100 if code in PERCENT_STATS else float(amount)
    rounded = round(value, 6)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)


__all__ = [
    "EnhancementOperation",
    "EnhancementPacket",
    "EnhancementPacketError",
    "PERCENT_STATS",
    "STAT_NAMES",
]
