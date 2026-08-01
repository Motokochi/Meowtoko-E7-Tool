"""Small stdlib client for the stateless packet-normalization API."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.core.live_packet_source import CapturedEnhancement


DEFAULT_PACKET_API_BASE_URL = (
    "https://llzucf97bg.execute-api.eu-west-1.amazonaws.com/v1"
)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RAW_BATCH_BYTES = 5 * 1024 * 1024
MAX_BATCH_CANDIDATES = 64


class PacketApiError(RuntimeError):
    pass


class PacketApiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_PACKET_API_BASE_URL,
        *,
        timeout: float = 15.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = opener

    def normalize_inventory(
        self,
        payloads: Sequence[bytes],
    ) -> Mapping[str, Any]:
        for batch in _payload_batches(payloads):
            response = self._post(
                "/inventory/normalize",
                {"transportCandidatesBase64": _encoded_payloads(batch)},
            )
            inventory = response.get("inventory")
            if isinstance(inventory, Mapping):
                return inventory
            if response.get("matched") is not False:
                raise PacketApiError("The packet service returned an invalid inventory.")
        raise PacketApiError(
            "No account inventory snapshot was recognized in the captured game traffic."
        )

    def inspect_enhancement(
        self,
        payloads: Sequence[bytes],
        expected_item_id: str | None,
    ) -> CapturedEnhancement | None:
        candidates = list(payloads)
        if not candidates:
            return None
        response = self._post(
            "/enhancement/normalize",
            {
                "transportCandidatesBase64": _encoded_payloads(candidates),
                **(
                    {"expectedItemId": expected_item_id}
                    if expected_item_id is not None
                    else {}
                ),
            },
        )
        if response.get("matched") is False:
            return None
        item_id = response.get("itemId")
        candidate_index = response.get("candidateIndex")
        if (
            not isinstance(item_id, str)
            or not item_id
            or isinstance(candidate_index, bool)
            or not isinstance(candidate_index, int)
            or not 0 <= candidate_index < len(candidates)
        ):
            raise PacketApiError("The packet service returned an invalid enhancement match.")
        if expected_item_id is not None and item_id != expected_item_id:
            return None
        return CapturedEnhancement(item_id=item_id, payload=candidates[candidate_index])

    def normalize_enhancement(
        self,
        packet: CapturedEnhancement,
        enhancement: int | None,
        initial_substat_count: int,
    ) -> Mapping[str, Any]:
        response = self._post(
            "/enhancement/normalize",
            {
                **({"enhancement": enhancement} if enhancement is not None else {}),
                "initialSubstatCount": initial_substat_count,
                "expectedItemId": packet.item_id,
                "transportCandidateBase64": base64.b64encode(packet.payload).decode("ascii"),
            },
        )
        roll_stats = response.get("enhancementRollStats")
        checkpoints = response.get("parsedCheckpoints")
        if (
            str(response.get("itemId", "")).strip() != packet.item_id
            or not isinstance(roll_stats, list)
            or not all(isinstance(stat, str) for stat in roll_stats)
            or not isinstance(checkpoints, list)
            or not all(isinstance(checkpoint, Mapping) for checkpoint in checkpoints)
        ):
            raise PacketApiError(
                "The packet service returned an invalid enhancement result."
            )
        return {
            "enhancementRollStats": list(roll_stats),
            "parsedCheckpoints": [dict(checkpoint) for checkpoint in checkpoints],
        }

    def _post(self, path: str, document: Mapping[str, Any]) -> Mapping[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Meowtoko-E7-Tool",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise PacketApiError(
                f"The packet service rejected the request (HTTP {error.code})."
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PacketApiError(
                "The packet service could not be reached. Check your internet connection."
            ) from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise PacketApiError("The packet service response was too large.")
        try:
            value = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PacketApiError("The packet service returned invalid JSON.") from error
        if not isinstance(value, Mapping):
            raise PacketApiError("The packet service returned an invalid response.")
        return value


def _encoded_payloads(payloads: Sequence[bytes]) -> list[str]:
    return [base64.b64encode(payload).decode("ascii") for payload in payloads]


def _payload_batches(payloads: Sequence[bytes]):
    batch: list[bytes] = []
    size = 0
    for payload in payloads:
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_RAW_BATCH_BYTES:
            continue
        if batch and (
            len(batch) >= MAX_BATCH_CANDIDATES
            or size + len(payload) > MAX_RAW_BATCH_BYTES
        ):
            yield batch
            batch = []
            size = 0
        batch.append(payload)
        size += len(payload)
    if batch:
        yield batch


__all__ = [
    "DEFAULT_PACKET_API_BASE_URL",
    "PacketApiClient",
    "PacketApiError",
]
