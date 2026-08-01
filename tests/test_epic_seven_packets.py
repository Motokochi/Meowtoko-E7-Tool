import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.core.enhancement_packets import EnhancementPacket, EnhancementPacketError
from src.core.live_packet_source import CapturedEnhancement, LivePacketSource, _TcpGroup


class EnhancementPacketTests(unittest.TestCase):
    def test_latest_operation_is_the_enhancement_event_and_substats_are_aggregated(self):
        event = EnhancementPacket.from_message({
            "equip": 123,
            "op": [
                ["max_hp_rate", 0.12],
                ["cri_dmg", 0.04],
                ["def", 31],
                ["acc", 0.04],
                ["speed", 3],
                ["speed", 2],
            ],
        })

        parsed = event.parsed_gear(3)

        self.assertEqual(event.item_id, "123")
        self.assertEqual(event.latest_roll_stat, "speed")
        self.assertEqual(parsed["_enhancement_event"]["statCode"], "speed")
        self.assertEqual(parsed["subs"][-1], {"stat": "Speed", "val": "5"})

    def test_invalid_packet_is_rejected_at_the_boundary(self):
        for message in (
            {},
            {"equip": 1, "op": []},
            {"equip": 1, "op": [["speed", True], ["cri", 0.03]]},
        ):
            with self.subTest(message=message), self.assertRaises(EnhancementPacketError):
                EnhancementPacket.from_message(message)

    def test_imported_rarity_separates_original_substats_from_existing_rolls(self):
        event = EnhancementPacket.from_message({
            "equip": 123,
            "op": [
                ["max_hp_rate", 0.12],
                ["cri_dmg", 0.04],
                ["def", 31],
                ["acc", 0.04],
                ["speed", 3],
                ["speed", 2],
                ["cri", 0.03],
                ["acc", 0.04],
            ],
        })

        self.assertEqual(
            [roll.stat_code for roll in event.enhancement_rolls(4)],
            ["speed", "cri", "acc"],
        )
        self.assertEqual(event.parsed_gear_at(6, 4)["enhance"], "+6")

    def test_three_substat_gear_counts_the_unlocked_fourth_stat_as_first_event(self):
        event = EnhancementPacket.from_message({
            "equip": 123,
            "op": [
                ["max_hp_rate", 0.12],
                ["cri_dmg", 0.04],
                ["def", 31],
                ["cri", 0.04],
                ["acc", 0.04],
                ["speed", 2],
            ],
        })

        self.assertEqual(
            [roll.stat_code for roll in event.enhancement_rolls(3)],
            ["acc", "speed"],
        )

    def test_tcp_group_reassembles_out_of_order_segments(self):
        group = _TcpGroup()

        group.add(100, b"pa")
        group.add(104, b"oad")
        group.add(102, b"yl")

        self.assertEqual(group.reassembled(), b"payload")

    def test_live_capture_uses_every_adapter_without_a_fragile_bpf(self):
        observed = []

        class FakeSniffer:
            running = False

            def __init__(self, **kwargs):
                observed.append(kwargs)
                self.callback = kwargs["started_callback"]

            def start(self):
                self.running = True
                self.callback()

            def stop(self):
                self.running = False

        scapy = types.ModuleType("scapy")
        scapy_all = types.ModuleType("scapy.all")
        scapy_all.AsyncSniffer = FakeSniffer
        scapy_all.IP = object()
        scapy_all.IPv6 = object()
        scapy_all.Raw = object()
        scapy_all.TCP = object()
        scapy_all.get_if_list = lambda: ["Ethernet", "LDPlayer"]
        scapy.all = scapy_all

        with patch.dict(sys.modules, {"scapy": scapy, "scapy.all": scapy_all}):
            source = LivePacketSource()
            source.start()
            source.stop()

        self.assertNotIn("filter", observed[0])
        self.assertEqual(observed[0]["iface"], ["Ethernet", "LDPlayer"])

    def test_live_capture_accepts_port_5222_ipv6_responses(self):
        source = self._source_with_layers()
        source._on_packet(self._packet(source, sport=5222, payload=b"opaque", ipv6=True))

        self.assertEqual(source.captured_payloads(), [b"opaque"])
        self.assertEqual(source.capture_status()["gamePacketsSeen"], 1)

    def test_live_capture_ignores_unrelated_server_ports(self):
        source = self._source_with_layers()
        source._on_packet(self._packet(source, sport=443, payload=b"private-web-data"))

        self.assertEqual(source.captured_payloads(), [])
        self.assertEqual(source.capture_status()["gamePacketsSeen"], 0)

    def test_enhancer_reads_only_payloads_after_its_action_boundary(self):
        received = []

        def inspect(payloads, expected_item_id):
            received.append((list(payloads), expected_item_id))
            return CapturedEnhancement("gear-1", payloads[-1])

        source = self._source_with_layers(LivePacketSource(inspect))
        source._on_packet(self._packet(source, sport=3333, payload=b"old", ack=1))
        source.mark_boundary()
        source._on_packet(self._packet(source, sport=3333, payload=b"new", ack=2))

        result = source.wait_for_enhancement(
            expected_item_id="gear-1",
            timeout=0.2,
            cancel_check=lambda: False,
        )

        self.assertEqual(result.item_id, "gear-1")
        self.assertEqual(received, [([b"new"], "gear-1")])

    @staticmethod
    def _source_with_layers(source=None):
        source = source or LivePacketSource()
        source._ip_layer = object()
        source._ipv6_layer = object()
        source._tcp_layer = object()
        source._raw_layer = object()
        return source

    @staticmethod
    def _packet(source, *, sport, payload, ack=1, ipv6=False):
        tcp = SimpleNamespace(sport=sport, dport=50000, seq=100, ack=ack)
        ip = SimpleNamespace(src="2001:db8::1" if ipv6 else "198.51.100.7", dst="192.0.2.2")
        raw = SimpleNamespace(load=payload)
        ip_layer = source._ipv6_layer if ipv6 else source._ip_layer

        class Packet:
            def haslayer(self, layer):
                return layer in {source._tcp_layer, ip_layer, source._raw_layer}

            def __getitem__(self, layer):
                if layer is source._tcp_layer:
                    return tcp
                return raw if layer is source._raw_layer else ip

        return Packet()


if __name__ == "__main__":
    unittest.main()
