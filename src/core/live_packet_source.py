"""Capture opaque Epic Seven response payloads for server-side decoding."""

from __future__ import annotations

import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


GAME_PORTS = frozenset({3333, 5222})
MAX_CAPTURE_BYTES = 32 * 1024 * 1024
MAX_GROUP_BYTES = 8 * 1024 * 1024
MAX_GROUPS = 256


class PacketCaptureUnavailable(RuntimeError):
    pass


class EnhancementPacketTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class CapturedEnhancement:
    item_id: str
    payload: bytes = field(repr=False)


@dataclass
class _TcpGroup:
    segments: dict[int, bytes] = field(default_factory=dict)
    revision: int = 0
    size: int = 0

    def add(self, sequence: int, payload: bytes) -> int:
        existing = self.segments.get(sequence)
        if existing is not None and len(existing) >= len(payload):
            return 0
        previous = len(existing) if existing is not None else 0
        self.segments[sequence] = payload
        delta = len(payload) - previous
        self.size += delta
        self.revision += 1
        return delta

    def reassembled(self) -> bytes:
        output = bytearray()
        next_sequence: int | None = None
        for sequence, payload in sorted(self.segments.items()):
            if next_sequence is None:
                output.extend(payload)
                next_sequence = sequence + len(payload)
                continue
            if sequence > next_sequence:
                break
            overlap = next_sequence - sequence
            if overlap < len(payload):
                output.extend(payload[overlap:])
                next_sequence += len(payload) - overlap
        return bytes(output)


class LivePacketSource:
    """Capture bounded server responses without decoding game data locally."""

    def __init__(
        self,
        enhancement_reader: Callable[
            [Sequence[bytes], str | None], CapturedEnhancement | None
        ] | None = None,
    ) -> None:
        self.enhancement_reader = enhancement_reader
        self._groups: OrderedDict[tuple[str, int, str, int, int], _TcpGroup] = OrderedDict()
        self._read_revisions: dict[tuple[str, int, str, int, int], int] = {}
        self._capture_bytes = 0
        self._condition = threading.Condition()
        self._sniffer: Any = None
        self._ip_layer: Any = None
        self._ipv6_layer: Any = None
        self._tcp_layer: Any = None
        self._raw_layer: Any = None
        self._packets_seen = 0
        self._game_packets_seen = 0
        self._decoded_messages = 0
        self._adapter_packets: Counter[str] = Counter()
        self._tcp_source_ports: Counter[int] = Counter()
        self.last_error: str | None = None

    def start(self) -> None:
        if self._sniffer is not None:
            return
        try:
            from scapy.all import AsyncSniffer, IP, IPv6, Raw, TCP, get_working_ifaces
        except ImportError as error:
            raise PacketCaptureUnavailable(
                "Packet capture components are missing. Reinstall Meowtoko E7 Tool, then install Npcap."
            ) from error

        started = threading.Event()
        self._ip_layer = IP
        self._ipv6_layer = IPv6
        self._tcp_layer = TCP
        self._raw_layer = Raw
        try:
            interfaces = list(get_working_ifaces())
            if not interfaces:
                raise PacketCaptureUnavailable("Npcap did not expose any network adapters.")
            self._sniffer = AsyncSniffer(
                iface=interfaces,
                prn=self._on_packet,
                store=False,
                started_callback=started.set,
            )
            self._sniffer.start()
            if not started.wait(3):
                raise PacketCaptureUnavailable("Packet capture did not start within three seconds.")
        except Exception as error:
            self.stop()
            raise PacketCaptureUnavailable(
                f"Packet capture could not start. Install Npcap and allow local capture: {error}"
            ) from error

    def stop(self) -> None:
        sniffer = self._sniffer
        self._sniffer = None
        if sniffer is not None and getattr(sniffer, "running", False):
            try:
                sniffer.stop()
            except Exception:
                pass
        with self._condition:
            self._groups.clear()
            self._read_revisions.clear()
            self._capture_bytes = 0
            self._condition.notify_all()

    def mark_boundary(self) -> None:
        """Ignore traffic captured before the next enhancement action."""

        with self._condition:
            self._read_revisions = {
                key: group.revision for key, group in self._groups.items()
            }

    def wait_for_enhancement(
        self,
        *,
        expected_item_id: str | None,
        timeout: float,
        cancel_check: Callable[[], bool],
    ) -> CapturedEnhancement:
        if self.enhancement_reader is None:
            raise PacketCaptureUnavailable("The private packet service is unavailable.")
        deadline = time.monotonic() + timeout
        while True:
            if cancel_check():
                raise EnhancementPacketTimeout("Enhancement packet wait was cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = f" Last capture error: {self.last_error}" if self.last_error else ""
                raise EnhancementPacketTimeout(
                    f"No matching enhancement packet arrived within {timeout:g} seconds.{detail}"
                )
            with self._condition:
                changed = [
                    (key, group.revision, group.reassembled())
                    for key, group in self._groups.items()
                    if group.revision > self._read_revisions.get(key, 0)
                ]
                if not changed:
                    self._condition.wait(min(0.1, remaining))
                    continue
            payloads = [payload for _key, _revision, payload in changed if payload]
            if not payloads:
                continue
            try:
                result = self.enhancement_reader(payloads, expected_item_id)
            except Exception as error:
                self.last_error = str(error)[:240]
                raise
            with self._condition:
                for key, revision, _payload in changed:
                    self._read_revisions[key] = max(
                        revision,
                        self._read_revisions.get(key, 0),
                    )
            if result is not None:
                if expected_item_id is not None and result.item_id != expected_item_id:
                    continue
                self._decoded_messages += 1
                return result

    def captured_payloads(self) -> list[bytes]:
        with self._condition:
            unique = {
                payload
                for group in self._groups.values()
                if (payload := group.reassembled())
            }
        return sorted(unique, key=len, reverse=True)

    def capture_status(self) -> dict[str, Any]:
        sniffer = self._sniffer
        with self._condition:
            groups = len(self._groups)
            capture_bytes = self._capture_bytes
            adapters = len(self._adapter_packets)
            source_ports = [
                {"port": port, "packets": count}
                for port, count in self._tcp_source_ports.most_common(8)
            ]
        return {
            "running": bool(sniffer is not None and getattr(sniffer, "running", False)),
            "packetsSeen": self._packets_seen,
            "gamePacketsSeen": self._game_packets_seen,
            "decodedMessages": self._decoded_messages,
            "capturedGroups": groups,
            "capturedBytes": capture_bytes,
            "activeAdapters": adapters,
            "observedTcpSourcePorts": source_ports,
            "lastError": self.last_error,
        }

    def _on_packet(self, packet: Any) -> None:
        try:
            self._packets_seen += 1
            adapter = str(getattr(packet, "sniffed_on", "") or "").strip()
            if adapter:
                with self._condition:
                    self._adapter_packets[adapter] += 1
            if not packet.haslayer(self._tcp_layer) or not packet.haslayer(self._raw_layer):
                return
            tcp = packet[self._tcp_layer]
            source_port = int(tcp.sport)
            with self._condition:
                self._tcp_source_ports[source_port] += 1
            if source_port not in GAME_PORTS:
                return
            payload = bytes(packet[self._raw_layer].load)
            if not payload:
                return
            if packet.haslayer(self._ip_layer):
                ip = packet[self._ip_layer]
            elif packet.haslayer(self._ipv6_layer):
                ip = packet[self._ipv6_layer]
            else:
                return
            key = (
                str(ip.src),
                source_port,
                str(ip.dst),
                int(tcp.dport),
                int(tcp.ack),
            )
            with self._condition:
                group = self._groups.setdefault(key, _TcpGroup())
                if group.size + len(payload) > MAX_GROUP_BYTES:
                    self.last_error = "A captured response exceeded the safe local size limit."
                    return
                self._capture_bytes += group.add(int(tcp.seq), payload)
                self._groups.move_to_end(key)
                while len(self._groups) > MAX_GROUPS or self._capture_bytes > MAX_CAPTURE_BYTES:
                    old_key, old_group = self._groups.popitem(last=False)
                    self._capture_bytes -= old_group.size
                    self._read_revisions.pop(old_key, None)
                self._game_packets_seen += 1
                self._condition.notify_all()
        except (AttributeError, TypeError, ValueError) as error:
            self.last_error = str(error)[:240]


def packet_capture_version() -> str | None:
    try:
        import scapy
    except ImportError:
        return None
    return str(getattr(scapy, "__version__", "unknown"))


__all__ = [
    "CapturedEnhancement",
    "EnhancementPacketTimeout",
    "GAME_PORTS",
    "LivePacketSource",
    "PacketCaptureUnavailable",
    "packet_capture_version",
]
