import base64
import json
import unittest
from urllib.error import URLError

from src.core.live_packet_source import CapturedEnhancement
from src.core.packet_api_client import PacketApiClient, PacketApiError


class FakeResponse:
    def __init__(self, document):
        self.body = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.body


class PacketApiClientTests(unittest.TestCase):
    def test_inventory_posts_opaque_candidates_and_returns_document(self):
        requests = []

        def open_request(request, *, timeout):
            requests.append((request, timeout))
            return FakeResponse({"matched": True, "inventory": {"items": [], "heroes": []}})

        client = PacketApiClient("https://example.test/v1/", timeout=4, opener=open_request)
        result = client.normalize_inventory([b"opaque-response"])

        request, timeout = requests[0]
        self.assertEqual(request.full_url, "https://example.test/v1/inventory/normalize")
        self.assertEqual(timeout, 4)
        self.assertEqual(
            json.loads(request.data),
            {"transportCandidatesBase64": [base64.b64encode(b"opaque-response").decode("ascii")]},
        )
        self.assertEqual(result, {"items": [], "heroes": []})

    def test_enhancement_inspection_returns_the_matching_opaque_candidate(self):
        requests = []

        def open_request(request, *, timeout):
            requests.append(request)
            return FakeResponse({"matched": True, "itemId": "77", "candidateIndex": 1})

        payloads = [b"unrelated", b"enhancement"]
        result = PacketApiClient(
            "https://example.test/v1",
            opener=open_request,
        ).inspect_enhancement(payloads, "77")

        self.assertEqual(result, CapturedEnhancement("77", b"enhancement"))
        self.assertEqual(json.loads(requests[0].data)["expectedItemId"], "77")

    def test_enhancement_normalization_keeps_decoding_server_side(self):
        requests = []

        def open_request(request, *, timeout):
            requests.append(request)
            return FakeResponse({
                "itemId": "77",
                "enhancementRollStats": ["speed"],
                "parsedCheckpoints": [{
                    "enhance": "+3",
                    "subs": [{"stat": "Speed", "val": "5"}],
                    "_enhancement_event": {"itemId": "77", "statCode": "speed"},
                }],
            })

        packet = CapturedEnhancement("77", b"opaque-enhancement-response")
        result = PacketApiClient(
            "https://example.test/v1",
            opener=open_request,
        ).normalize_enhancement(packet, None, 4)

        body = json.loads(requests[0].data)
        self.assertNotIn("message", body)
        self.assertEqual(body["expectedItemId"], "77")
        self.assertEqual(body["initialSubstatCount"], 4)
        self.assertEqual(result["enhancementRollStats"], ["speed"])

    def test_network_errors_are_stable_and_do_not_expose_urls(self):
        def fail(_request, *, timeout):
            raise URLError("private-network-detail")

        client = PacketApiClient("https://private.example", opener=fail)

        with self.assertRaises(PacketApiError) as raised:
            client.normalize_inventory([b"candidate"])

        self.assertNotIn("private", str(raised.exception))
        self.assertIn("internet connection", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
