"""Message centre: normalisation and the provider/route around it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import message  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402

PAYLOAD = {
    "messages": [
        {"msg_id": 11, "type": 6, "title": "\u6d17\u6da4\u5b8c\u6210",
         "content": "\u6d17\u8863\u673a", "did": "d1", "ctime": 100,
         "is_new": 1, "img_url": "https://x/y.png"},
        {"msg_id": 12, "type": 13, "title": "\u573a\u666f\u6267\u884c\u5931\u8d25",
         "content": "", "did": "", "ctime": 200, "is_new": 0},
        {"msg_id": 13, "type": 99, "title": "\u672a\u77e5\u7c7b\u578b",
         "content": "", "did": "d2", "ctime": 50, "is_new": 1},
        {"title": "\u6ca1\u6709 id \u7684\u810f\u6570\u636e", "ctime": 999},
    ]
}


class TestNormalise(unittest.TestCase):
    def setUp(self):
        self.data = message.normalise(PAYLOAD, {"d1": "\u6d17\u8863\u673a",
                                                "d2": "\u6ce1\u6ce1"})

    def test_drops_records_without_id(self):
        self.assertEqual([m["id"] for m in self.data["messages"]],
                         ["12", "11", "13"])

    def test_sorted_newest_first(self):
        times = [m["time"] for m in self.data["messages"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_type_labels(self):
        by_id = {m["id"]: m for m in self.data["messages"]}
        self.assertEqual(by_id["11"]["type_label"], "\u8bbe\u5907\u6d88\u606f")
        self.assertEqual(by_id["12"]["type_label"], "\u573a\u666f\u901a\u77e5")
        self.assertEqual(by_id["13"]["type_label"], "\u6d88\u606f",
                         "unknown types must not be mislabelled")

    def test_device_name_is_resolved(self):
        by_id = {m["id"]: m for m in self.data["messages"]}
        self.assertEqual(by_id["11"]["device"], "\u6d17\u8863\u673a")
        self.assertEqual(by_id["12"]["device"], "")

    def test_unread_count(self):
        self.assertEqual(self.data["unread"], 2)

    def test_types_summary(self):
        self.assertEqual(self.data["types"], [
            {"type": 6, "label": "\u8bbe\u5907\u6d88\u606f", "count": 1},
            {"type": 13, "label": "\u573a\u666f\u901a\u77e5", "count": 1},
            {"type": 99, "label": "\u6d88\u606f", "count": 1},
        ])

    def test_types_sort_by_count_then_id(self):
        payload = {"messages": [
            {"msg_id": 1, "type": 6, "title": "a", "ctime": 3},
            {"msg_id": 2, "type": 6, "title": "b", "ctime": 2},
            {"msg_id": 3, "type": 13, "title": "c", "ctime": 1},
        ]}
        types = message.normalise(payload)["types"]
        self.assertEqual([item["type"] for item in types], [6, 13])
        self.assertEqual(types[0]["count"], 2)

    def test_empty_payload(self):
        self.assertEqual(message.normalise(None),
                         {"messages": [], "types": [], "unread": 0})


class StubClient:
    base_url = "https://stub.invalid"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def messages(self, timestamp=None, limit=30):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return PAYLOAD

    def mark_messages_read(self, dids, home_id=0, home_owner=0):
        self.read_call = (list(dids), home_id, home_owner)
        return True

    def delete_message(self, msg_id, msg_type=0, did=""):
        self.delete_call = (str(msg_id), msg_type, did)
        return True

    def homes(self):
        return {"homelist": [{"id": "h1", "name": "\u5bb6", "uid": 42}]}

    def devices(self, home_id=None, start_did=None, limit=200):
        return {"list": [{"did": "d1", "model": "minij.washer.v5",
                          "name": "\u6d17\u8863\u673a", "isOnline": True}]}


class TestCloudProviderMessages(unittest.TestCase):
    def test_messages_are_normalised_and_cached(self):
        client = StubClient()
        provider = web_server.CloudProvider(None, client=client)
        first = provider.messages()
        second = provider.messages()
        self.assertEqual(len(first["messages"]), 3)
        self.assertEqual(first["unread"], 2)
        self.assertEqual(client.calls, 1, "second read hits the cache")
        self.assertIs(first, second)

    def test_refresh_bypasses_the_cache(self):
        client = StubClient()
        provider = web_server.CloudProvider(None, client=client)
        provider.messages()
        provider.refresh_messages()
        self.assertEqual(client.calls, 2)

    def test_mark_read_uses_the_listed_devices(self):
        client = StubClient()
        provider = web_server.CloudProvider(None, client=client)
        provider.messages()
        result = provider.mark_messages_read()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["marked"], 2, "only the two devices with dids")
        dids, home_id, owner = client.read_call
        self.assertEqual(dids, ["d1", "d2"])
        self.assertEqual(str(home_id), "h1")
        self.assertEqual(owner, 42)

    def test_mark_read_without_devices_is_refused(self):
        client = StubClient()
        client.messages = lambda **kwargs: {"messages": [
            {"msg_id": 1, "type": 13, "title": "x", "did": "", "ctime": 1}]}
        provider = web_server.CloudProvider(None, client=client)
        result = provider.mark_messages_read()
        self.assertFalse(result["ok"])

    def test_delete_message_passes_type_and_did(self):
        client = StubClient()
        provider = web_server.CloudProvider(None, client=client)
        result = provider.delete_message("11")
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.delete_call, ("11", 6, "d1"))

    def test_delete_unknown_message(self):
        provider = web_server.CloudProvider(None, client=StubClient())
        result = provider.delete_message("nope")
        self.assertFalse(result["ok"])

    def test_device_messages_use_the_listing_endpoint(self):
        client = StubClient()
        client.device_messages = lambda did, msg_type=6, timestamp=0, limit=20: {
            "messages": [{"msg_id": 21, "type": 6, "title": "\u6d17\u6da4\u5b8c\u6210",
                            "did": did, "ctime": 300, "is_new": 0}]}
        provider = web_server.CloudProvider(None, client=client)
        data = provider.device_messages("d1", limit=5)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["device"], "\u6d17\u8863\u673a")
        self.assertEqual(data["messages"][0]["time"], 300)

    def test_device_messages_failure_is_empty(self):
        class Broken(StubClient):
            def device_messages(self, did, msg_type=6, timestamp=0, limit=20):
                raise RuntimeError("boom")

        provider = web_server.CloudProvider(None, client=Broken())
        self.assertEqual(provider.device_messages("d1"),
                         {"messages": [], "types": [], "unread": 0})

    def test_unread_count_reuses_the_cache(self):
        client = StubClient()
        provider = web_server.CloudProvider(None, client=client)
        self.assertEqual(provider.unread_count(), 2)
        provider.unread_count()
        self.assertEqual(client.calls, 1, "polling must not hit the cloud twice")

    def test_failure_degrades_to_empty(self):
        provider = web_server.CloudProvider(None, client=StubClient(fail=True))
        self.assertEqual(provider.messages(),
                         {"messages": [], "types": [], "unread": 0})


if __name__ == "__main__":
    unittest.main()
