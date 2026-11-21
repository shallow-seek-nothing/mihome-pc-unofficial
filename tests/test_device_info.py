"""Device info: field mapping, token masking, plugin text count."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.web import server as web_server  # noqa: E402


class StubClient:
    base_url = "https://stub.invalid"

    def __init__(self, props=None, fail_props=False):
        self.props_payload = props if props is not None else [
            {"did": "d1", "siid": 1, "piid": 1, "value": "minij", "code": 0},
            {"did": "d1", "siid": 1, "piid": 2, "value": "minij.washer.v5", "code": 0},
            {"did": "d1", "siid": 1, "piid": 3, "value": "SN-1", "code": 0},
            {"did": "d1", "siid": 1, "piid": 4, "value": "2.3.0", "code": 0},
            {"did": "d1", "siid": 1, "piid": 9, "value": "ignored", "code": 0},
        ]
        self.fail_props = fail_props

    def homes(self):
        return {"homelist": [{"id": "h1", "name": "\u5bb6", "uid": 42,
                               "icon": "style_1_favorites",
                               "smart_room_background": "style_1",
                               "smart_scene_background": "style_1",
                              "roomlist": [{"id": 1, "name": "\u9633\u53f0",
                                             "dids": ["d1"]}]}]}

    def devices(self, home_id=None, start_did=None, limit=200):
        return {"list": [{"did": "d1", "model": "minij.washer.v5",
                          "name": "\u6d17\u8863\u673a", "isOnline": True,
                          "localip": "", "mac": "AA:BB:CC:DD:EE:FF",
                          "token": "0123456789abcdef0123456789abcdef",
                          "spec_type": "urn:miot-spec-v2:device:washer:0000A01F:x:1"}]}

    def props(self, items, datasource=1):
        if self.fail_props:
            raise RuntimeError("boom")
        return self.props_payload

    def add_room(self, home_owner, parent_id, name, background=""):
        self.room_calls = getattr(self, "room_calls", [])
        self.room_calls.append(("add", home_owner, parent_id, name))
        if getattr(self, "fail_room", False):
            raise RuntimeError("room boom")
        return {"roomid": "900000000001", "did": []}

    def delete_rooms(self, room_ids, home_owner):
        self.room_calls = getattr(self, "room_calls", [])
        self.room_calls.append(("delete", home_owner, list(room_ids)))
        return {"affect": 1}

    def move_devices_to_room(self, room_id, dids, home_owner):
        self.room_calls = getattr(self, "room_calls", [])
        self.room_calls.append(("move", home_owner, room_id, list(dids)))
        return "ok"

    def rename_device(self, did, name, is_group=False):
        self.rename_call = (str(did), name, bool(is_group))
        if getattr(self, "fail_rename", False):
            raise RuntimeError("rename boom")
        return True

    def rename_home(self, home_id, home_owner, name, icon=None,
                    smart_room_background=None, smart_scene_background=None):
        self.home_rename = (str(home_id), name, icon,
                            smart_room_background, smart_scene_background)
        return {"id": str(home_id)}

    def set_room(self, home_owner, room_id, name=None, icon=None,
                 background=None, add=None, delete=None, app_ver=11):
        self.room_calls = getattr(self, "room_calls", [])
        self.room_calls.append((home_owner, room_id, name))
        if getattr(self, "fail_room", False):
            raise RuntimeError("room boom")
        return {"affect": 1, "add": [], "delete": []}


class TestRenameRoom(unittest.TestCase):
    def setUp(self):
        self.client = StubClient()
        self.provider = web_server.CloudProvider(None, client=self.client)
        # the owner comes from the home list, which rooms() caches
        self.provider.rooms()

    def test_add_room_extracts_roomid(self):
        result = self.provider.add_room("\u4e66\u623f")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["room_id"], "900000000001",
                         "the gateway answers {roomid: ...}")
        call = self.client.room_calls[-1]
        self.assertEqual(call[0], "add")
        self.assertEqual(call[2], "h1")           # parent_id is the home
        self.assertEqual(call[3], "\u4e66\u623f")

    def test_add_room_rejects_blank(self):
        result = self.provider.add_room("  ")
        self.assertFalse(result["ok"])
        self.assertFalse(getattr(self.client, "room_calls", []))

    def test_delete_room(self):
        self.assertTrue(self.provider.delete_room("900000000001")["ok"])
        self.assertEqual(self.client.room_calls[-1][2], ["900000000001"])

    def test_move_device(self):
        self.assertTrue(self.provider.move_device("d1", "900000000001")["ok"])
        call = self.client.room_calls[-1]
        self.assertEqual(call[0], "move")
        self.assertEqual(str(call[2]), "900000000001")
        self.assertEqual(call[3], ["d1"])

    def test_rename_passes_owner_and_name(self):
        result = self.provider.rename_room(900000000002, "\u5ba2\u5385")
        self.assertTrue(result["ok"], result)
        owner, room_id, name = self.client.room_calls[-1]
        self.assertEqual(str(owner), "42")
        self.assertEqual(str(room_id), "900000000002")
        self.assertEqual(name, "\u5ba2\u5385")

    def test_blank_name_is_refused_without_a_request(self):
        result = self.provider.rename_room(1, "   ")
        self.assertFalse(result["ok"])
        self.assertFalse(getattr(self.client, "room_calls", []))

    def test_too_long_name_is_refused(self):
        result = self.provider.rename_room(1, "x" * 21)
        self.assertFalse(result["ok"])

    def test_failure_is_reported(self):
        self.client.fail_room = True
        result = self.provider.rename_room(1, "\u6d4b\u8bd5")
        self.assertFalse(result["ok"])
        self.assertIn("room boom", result["message"])


class TestLastSeen(unittest.TestCase):
    def test_newest_timestamp_wins(self):
        self.assertEqual(web_server._newest_seen({"2.1": 100, "2.2": 200}), 200)

    def test_empty_or_dirty_maps(self):
        self.assertEqual(web_server._newest_seen(None), 0)
        self.assertEqual(web_server._newest_seen({}), 0)
        self.assertEqual(web_server._newest_seen({"2.1": None}), 0)

    def test_devices_always_carry_the_field(self):
        provider = web_server.CloudProvider(None, client=StubClient())
        self.assertIn("last_seen", provider.devices()[0],
                      "even the spec-unavailable fallback must have it")


class TestRename(unittest.TestCase):
    def setUp(self):
        self.client = StubClient()
        self.provider = web_server.CloudProvider(None, client=self.client)

    def test_rename_device_passes_the_name(self):
        result = self.provider.rename_device("d1", "\u5ba2\u5385\u706f")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.client.rename_call,
                         ("d1", "\u5ba2\u5385\u706f", False))

    def test_rename_device_blank_is_refused(self):
        self.assertFalse(self.provider.rename_device("d1", "  ")["ok"])
        self.assertFalse(getattr(self.client, "rename_call", None))

    def test_rename_device_failure(self):
        self.client.fail_rename = True
        result = self.provider.rename_device("d1", "x")
        self.assertFalse(result["ok"])
        self.assertIn("rename boom", result["message"])

    def test_rename_home_keeps_icon_and_background(self):
        result = self.provider.rename_home("\u65b0\u5bb6\u5ead")
        self.assertTrue(result["ok"], result)
        home_id, name, icon, room_bg, scene_bg = self.client.home_rename
        self.assertEqual(home_id, "h1")
        self.assertEqual(name, "\u65b0\u5bb6\u5ead")
        self.assertIsNotNone(icon, "icon must be sent back, otherwise it resets")


class TestDeviceInfo(unittest.TestCase):
    def setUp(self):
        self.client = StubClient()
        self.provider = web_server.CloudProvider(None, client=self.client)

    def test_fields(self):
        info = self.provider.device_info("d1")
        self.assertEqual(info["name"], "\u6d17\u8863\u673a")
        self.assertEqual(info["room"], "\u9633\u53f0")
        self.assertEqual(info["model"], "minij.washer.v5")
        self.assertEqual(info["info"]["manufacturer"], "minij")
        self.assertEqual(info["info"]["serial-number"], "SN-1")
        self.assertEqual(info["info"]["firmware-revision"], "2.3.0")
        self.assertEqual(info["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(info["localip"], "")
        self.assertFalse(info["presence"]["checked"],
                         "no localip -> no TCP probe (keeps tests offline)")

    def test_token_is_masked(self):
        info = self.provider.device_info("d1")
        self.assertTrue(info["token"].startswith("012345"))
        self.assertIn("32 hex", info["token"])
        self.assertNotIn("abcdef0123456789", info["token"])

    def test_unknown_property_is_ignored(self):
        info = self.provider.device_info("d1")
        self.assertEqual(sorted(info["info"]),
                         ["firmware-revision", "manufacturer", "model",
                          "serial-number"])

    def test_props_failure_degrades(self):
        provider = web_server.CloudProvider(None, client=StubClient(fail_props=True))
        info = provider.device_info("d1")
        self.assertEqual(info["info"]["manufacturer"], "")
        self.assertEqual(info["model"], "minij.washer.v5")

    def test_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.provider.device_info("nope")


if __name__ == "__main__":
    unittest.main()
