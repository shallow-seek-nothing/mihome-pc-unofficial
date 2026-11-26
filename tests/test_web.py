"""Web layer tests: run the real HTTP server on an ephemeral port.

Using the real socket (rather than calling handler methods directly) is what
makes these tests worth having -- routing, JSON encoding, status codes and the
path-traversal guard all get exercised.
"""
import json
import os
import sys
import tempfile
import threading
import unittest

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
except ImportError:                                  # pragma: no cover
    from urllib2 import Request, urlopen, HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import model as device_model  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402
from mihome_pc.xiaomi import auth as auth_mod  # noqa: E402
from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "specs")


class ServerFixture:
    """Starts a server with the demo provider on a free port.

    Pass ``server=`` to drive a server built by the caller (a custom
    ``credentials_path``, say) with the same request helpers.
    """

    def __init__(self, provider=None, server=None):
        if server is None:
            self.provider = provider or web_server.DemoProvider()
            self.server = web_server.create_server(provider=self.provider, port=0)
        else:
            self.provider = None
            self.server = server
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def url(self, path):
        return "http://%s:%d%s" % (self.host, self.port, path)

    def get(self, path, raw=False):
        response = urlopen(self.url(path), timeout=10)
        body = response.read()
        return body if raw else json.loads(body.decode("utf-8"))

    def post(self, path, payload, headers=None):
        data = json.dumps(payload).encode("utf-8")
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        request = Request(self.url(path), data=data, headers=merged)
        response = urlopen(request, timeout=10)
        return json.loads(response.read().decode("utf-8"))

    def status_of(self, path, method="GET", payload=None, headers=None):
        try:
            if method == "GET":
                urlopen(self.url(path), timeout=10)
            else:
                data = json.dumps(payload or {}).encode("utf-8")
                merged = {"Content-Type": "application/json"}
                merged.update(headers or {})
                urlopen(Request(self.url(path), data=data, headers=merged), timeout=10)
            return 200
        except HTTPError as exc:
            return exc.code


class WebTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Hermetic data dir: favourites/availability writes must never touch
        # the developer's real ~/.mihome-pc.
        cls.tmp = tempfile.TemporaryDirectory()
        cls._original_data_dir = web_server._data_dir
        web_server._data_dir = lambda: cls.tmp.name
        cls.fixture = ServerFixture().start()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()
        web_server._data_dir = cls._original_data_dir
        cls.tmp.cleanup()


class TestStaticFiles(WebTestCase):
    """静态文件只能读 STATIC_DIR 里的东西。这个服务没有鉴权，所以路径必须自己守住。"""

    def test_serves_real_assets(self):
        page = self.fixture.get("/", raw=True)
        self.assertIn(b"<!DOCTYPE html>", page)
        self.assertEqual(self.fixture.status_of("/static/app.js"), 200)

    def test_rejects_dotdot(self):
        self.assertEqual(self.fixture.status_of("/static/../mihome_pc/__init__.py"),
                         400)

    def test_rejects_drive_letter_absolute_path(self):
        # Windows: 带盘符的绝对路径会被 os.path.join 当成目标
        self.assertEqual(
            self.fixture.status_of("/static/C:/Windows/win.ini"), 400)

    def test_rejects_backslash_traversal(self):
        self.assertEqual(
            self.fixture.status_of("/static/..\\mihome_pc\\__init__.py"), 400)


class TestRefresh(WebTestCase):
    """``/api/refresh`` is what the detail page polls and what a focus event
    triggers, so its contract (always ``{state: {...}}``) must not drift."""

    def test_refresh_returns_a_state_dict(self):
        payload = self.fixture.post("/api/refresh", {"did": "demo-washer"})
        self.assertIn("state", payload)
        self.assertIsInstance(payload["state"], dict)

    def test_refresh_unknown_device_is_404(self):
        self.assertEqual(
            self.fixture.status_of("/api/refresh", "POST", {"did": "nope"}),
            404)


class TestDemoEmptyMode(unittest.TestCase):
    """MIHOME_PC_DEMO_EMPTY=1 is how the empty states get exercised."""

    def setUp(self):
        self.original = os.environ.get("MIHOME_PC_DEMO_EMPTY")

    def tearDown(self):
        if self.original is None:
            os.environ.pop("MIHOME_PC_DEMO_EMPTY", None)
        else:
            os.environ["MIHOME_PC_DEMO_EMPTY"] = self.original

    def test_flag_empties_every_list(self):
        os.environ["MIHOME_PC_DEMO_EMPTY"] = "1"
        provider = web_server.DemoProvider()
        self.assertTrue(provider.empty)
        self.assertEqual(provider.devices(), [])
        self.assertEqual(provider.scenes()["scenes"], [])
        self.assertEqual(provider.scenes()["automations"], [])
        self.assertEqual(provider.messages()["messages"], [])
        self.assertEqual(provider.rooms()["rooms"], [])

    def test_flag_off_keeps_the_demo_data(self):
        os.environ.pop("MIHOME_PC_DEMO_EMPTY", None)
        provider = web_server.DemoProvider()
        self.assertFalse(provider.empty)
        self.assertGreaterEqual(len(provider.devices()), 1)

    def test_zero_string_means_off(self):
        os.environ["MIHOME_PC_DEMO_EMPTY"] = "0"
        self.assertFalse(web_server.DemoProvider().empty)


class TestStatus(WebTestCase):
    def test_demo_status(self):
        payload = self.fixture.get("/api/status")
        self.assertTrue(payload["demo"])
        self.assertFalse(payload["logged_in"])

    def test_login_block_present(self):
        payload = self.fixture.get("/api/status")
        self.assertIn("login", payload)


class TestDeviceList(WebTestCase):
    def test_devices_are_served(self):
        payload = self.fixture.get("/api/devices")
        devices = payload["devices"]
        self.assertGreaterEqual(len(devices), 1, "fixtures should yield demo devices")
        for device in devices:
            self.assertTrue(device.get("did"))
            self.assertTrue(device.get("name"))

    def test_device_has_controls(self):
        payload = self.fixture.get("/api/devices")
        first = payload["devices"][0]
        self.assertTrue(first.get("controls"), "each device should expose controls")
        self.assertTrue(first.get("groups"))


class TestDeviceDetail(WebTestCase):
    def test_detail_includes_state_and_blocked(self):
        payload = self.fixture.get("/api/device?did=demo-dishwasher")
        self.assertEqual(payload["did"], "demo-dishwasher")
        self.assertIn("controls", payload)
        self.assertIn("state", payload)
        self.assertIn("blocked", payload)

    def test_blocked_reason_is_exposed(self):
        payload = self.fixture.get("/api/device?did=demo-dishwasher")
        # the demo starts the dishwasher in the "off" state (status = 6)
        self.assertIn("2.4", payload["blocked"])
        self.assertTrue(payload["blocked"]["2.4"])

    def test_availability_endpoint(self):
        payload = self.fixture.get("/api/availability?did=demo-dishwasher")
        rules = payload["availability"]
        self.assertIsNotNone(rules)
        self.assertTrue(rules["blockers"])

    def test_unknown_device_is_404(self):
        self.assertEqual(self.fixture.status_of("/api/device?did=nope"), 404)

    def test_missing_did_is_400(self):
        self.assertEqual(self.fixture.status_of("/api/device"), 400)


class TestSetProperty(WebTestCase):
    def test_write_allowed_control(self):
        before = self.fixture.get("/api/device?did=demo-dishwasher")
        self.assertNotIn("2.1", before["blocked"])
        payload = self.fixture.post("/api/set", {
            "did": "demo-dishwasher", "siid": 2, "piid": 1, "value": True})
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["state"]["2.1"], True)

    def test_write_blocked_control_is_refused_locally(self):
        payload = self.fixture.post("/api/set", {
            "did": "demo-dishwasher", "siid": 2, "piid": 4, "value": 1})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], -5000)
        self.assertTrue(payload["message"], "a refusal should explain itself")

    def test_write_unknown_property(self):
        payload = self.fixture.post("/api/set", {
            "did": "demo-dishwasher", "siid": 99, "piid": 99, "value": 1})
        self.assertFalse(payload["ok"])

    def test_missing_fields_is_400(self):
        self.assertEqual(self.fixture.status_of("/api/set", "POST", {"did": "x"}), 400)


class TestStatic(WebTestCase):
    def test_index_served(self):
        body = self.fixture.get("/", raw=True).decode("utf-8")
        self.assertIn("<title>", body)
        self.assertIn("app.js", body)

    def test_javascript_served(self):
        body = self.fixture.get("/static/app.js", raw=True).decode("utf-8")
        self.assertIn("function", body)

    def test_css_served(self):
        body = self.fixture.get("/static/style.css", raw=True).decode("utf-8")
        self.assertIn("--accent", body)

    def test_unknown_static_is_404(self):
        self.assertEqual(self.fixture.status_of("/static/nope.js"), 404)

    def test_path_traversal_blocked(self):
        code = self.fixture.status_of("/static/../server.py")
        self.assertIn(code, (400, 404), "must not hand out the server source")


class TestUnknownRoutes(WebTestCase):
    def test_api_404(self):
        self.assertEqual(self.fixture.status_of("/api/nothing"), 404)

    def test_post_404(self):
        self.assertEqual(self.fixture.status_of("/api/nothing", "POST", {}), 404)


class TestLauncher(unittest.TestCase):
    def test_find_chromium_returns_none_or_path(self):
        from mihome_pc.web import launcher
        found = launcher.find_chromium()
        if found is not None:
            self.assertTrue(os.path.exists(found))

    def test_open_ui_never_raises(self):
        from mihome_pc.web import launcher
        # prefer_app=False keeps this from spawning a real browser in CI
        result = launcher.open_ui("http://127.0.0.1:1/", prefer_app=False)
        self.assertIsInstance(result, str)


class TestParseActions(unittest.TestCase):
    def test_single_device_fields_become_one_spec(self):
        specs, error = web_server._parse_actions(
            {"did": "d1", "siid": "2", "piid": "1", "value": True})
        self.assertEqual(error, "")
        self.assertEqual(specs, [{"did": "d1", "kind": "prop", "siid": 2,
                                  "piid": 1, "aiid": None, "value": True,
                                  "inputs": None}])

    def test_action_list_is_passed_through(self):
        specs, error = web_server._parse_actions({"actions": [
            {"did": "d1", "siid": 2, "piid": 1, "value": False},
            {"did": "d2", "kind": "action", "siid": 3, "aiid": 4,
             "inputs": ["x"]},
        ]})
        self.assertEqual(error, "")
        self.assertEqual([item["did"] for item in specs], ["d1", "d2"])
        self.assertEqual(specs[1]["kind"], "action")
        self.assertEqual(specs[1]["inputs"], ["x"])

    def test_errors(self):
        self.assertEqual(web_server._parse_actions({"actions": "x"})[1],
                         "actions must be a list")
        self.assertEqual(web_server._parse_actions({"actions": [{}]})[1],
                         "each action needs a did")
        self.assertEqual(
            web_server._parse_actions({"actions": [{"did": "d", "kind": "x",
                                       "siid": 1}]})[1],
            "kind must be prop or action")
        self.assertEqual(
            web_server._parse_actions({"actions": [{"did": "d",
                                       "siid": "x"}]})[1],
            "siid/piid/aiid must be integers")

    def test_no_actions(self):
        self.assertEqual(web_server._parse_actions({}), ([], ""))


class TestRenameEndpoints(WebTestCase):
    def test_device_rename(self):
        result = self.fixture.post("/api/device/rename",
                                   {"did": "demo-washer", "name": "\u6d17\u8863\u673a2"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fixture.status_of("/api/device/rename", "POST",
                                                 {"did": "demo-washer"}), 400)

    def test_home_rename(self):
        result = self.fixture.post("/api/home/rename", {"name": "\u65b0\u5bb6"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fixture.status_of("/api/home/rename", "POST", {}), 400)


class TestRoomRename(WebTestCase):
    def test_demo_add_delete_move(self):
        added = self.fixture.post("/api/room/add", {"name": "\u4e66\u623f"})
        self.assertTrue(added["ok"], added)
        moved = self.fixture.post("/api/room/move",
                                  {"did": "demo-washer", "room_id": 1})
        self.assertTrue(moved["ok"], moved)
        deleted = self.fixture.post("/api/room/delete", {"room_id": 1})
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(self.fixture.status_of("/api/room/add", "POST", {}), 400)
        self.assertEqual(self.fixture.status_of("/api/room/delete", "POST", {}), 400)
        self.assertEqual(self.fixture.status_of("/api/room/move", "POST",
                                                 {"did": "demo-washer"}), 400)

    def test_demo_rename(self):
        result = self.fixture.post("/api/room/rename",
                                   {"room_id": 1, "name": "\u5ba2\u5385"})
        self.assertTrue(result["ok"], result)

    def test_demo_rejects_blank(self):
        result = self.fixture.post("/api/room/rename",
                                   {"room_id": 1, "name": "  "})
        self.assertFalse(result["ok"])

    def test_missing_fields_is_400(self):
        self.assertEqual(self.fixture.status_of("/api/room/rename", "POST",
                                                 {"room_id": 1}), 400)


class TestDeviceInfo(WebTestCase):
    def test_demo_device_info(self):
        payload = self.fixture.get("/api/device/info?did=demo-washer")
        self.assertEqual(payload["did"], "demo-washer")
        self.assertTrue(payload["model"])
        for key in ("name", "urn", "info", "plugin", "last_report"):
            self.assertIn(key, payload)
        self.assertIn("firmware-revision", payload["info"])
        self.assertIn("texts", payload["plugin"])

    def test_missing_did_is_400(self):
        self.assertEqual(self.fixture.status_of("/api/device/info"), 400)

    def test_unknown_device_is_404(self):
        self.assertEqual(
            self.fixture.status_of("/api/device/info?did=nope"), 404)


class TestMessages(WebTestCase):
    def test_demo_messages_are_listed(self):
        payload = self.fixture.get("/api/messages")
        self.assertGreaterEqual(len(payload["messages"]), 1)
        first = payload["messages"][0]
        for key in ("id", "title", "type_label", "time"):
            self.assertIn(key, first)
        self.assertIn("unread", payload)

    def test_demo_mark_read_and_delete(self):
        marked = self.fixture.post("/api/messages/read", {})
        self.assertTrue(marked["ok"], marked)
        self.assertEqual(self.fixture.get("/api/messages")["unread"], 0)
        first = self.fixture.get("/api/messages")["messages"][0]
        deleted = self.fixture.post("/api/messages/delete",
                                    {"msg_id": first["id"]})
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(self.fixture.status_of("/api/messages/delete", "POST", {}), 400)

    def test_messages_expose_type_summary(self):
        payload = self.fixture.get("/api/messages")
        self.assertIn("types", payload)
        self.assertGreaterEqual(len(payload["types"]), 1)
        first = payload["types"][0]
        for key in ("type", "label", "count"):
            self.assertIn(key, first)

    def test_members_endpoint(self):
        payload = self.fixture.get("/api/members")
        self.assertIn("members", payload)
        self.assertIn("count", payload)

    def test_unread_endpoint(self):
        payload = self.fixture.get("/api/messages/unread")
        self.assertIn("unread", payload)
        self.assertGreaterEqual(payload["unread"], 0)

    def test_device_messages_endpoint(self):
        payload = self.fixture.get("/api/messages/device?did=demo-washer")
        self.assertIn("messages", payload)
        self.assertEqual(self.fixture.status_of("/api/messages/device"), 400)

    def test_refresh_query_is_accepted(self):
        payload = self.fixture.get("/api/messages?refresh=1")
        self.assertIn("messages", payload)


class TestScenes(WebTestCase):
    """The scenes page is pure UI over two endpoints; both must answer."""

    def test_demo_scenes_are_listed(self):
        payload = self.fixture.get("/api/scenes")
        scenes = payload["scenes"]
        self.assertGreaterEqual(len(scenes), 2)
        self.assertTrue(payload["home"]["name"])
        first = scenes[0]
        for key in ("id", "name", "category_label", "devices", "actions"):
            self.assertIn(key, first)
        self.assertTrue(first["devices"])

    def test_running_a_scene_succeeds_in_demo(self):
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        result = self.fixture.post("/api/scene/run", {"scene_id": scene_id})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["code"], 0)

    def test_unknown_scene_is_refused(self):
        result = self.fixture.post("/api/scene/run", {"scene_id": "nope"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["message"])

    def test_missing_scene_id_is_400(self):
        self.assertEqual(self.fixture.status_of("/api/scene/run", "POST", {}), 400)

    def test_scene_reorder_in_demo(self):
        scenes = self.fixture.get("/api/scenes")["scenes"]
        ids = [scene["id"] for scene in scenes]
        result = self.fixture.post("/api/scene/reorder", {"scene_ids": ids})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fixture.status_of("/api/scene/reorder", "POST",
                                                 {}), 400)

    def test_favorites_in_demo(self):
        payload = self.fixture.get("/api/favorites")
        self.assertIn("dids", payload)
        did = self.fixture.get("/api/devices")["devices"][0]["did"]
        first = self.fixture.post("/api/favorites/toggle", {"did": did})
        self.assertTrue(first["ok"], first)
        self.assertEqual(self.fixture.get("/api/devices")["devices"][0]["did"],
                         did)
        back = self.fixture.post("/api/favorites/toggle", {"did": did})
        self.assertFalse(back["pinned"])
        self.assertEqual(self.fixture.status_of("/api/favorites/toggle", "POST",
                                                 {}), 400)

    def test_scene_icons_in_demo(self):
        payload = self.fixture.get("/api/scene/icons")
        self.assertGreaterEqual(len(payload["icons"]), 1)
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        applied = self.fixture.post("/api/scene/icon",
                                    {"scene_id": scene_id,
                                     "icon_url": "https://cdn/a.png"})
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(self.fixture.status_of("/api/scene/icon", "POST", {}), 400)

    def test_scene_move_in_demo(self):
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        moved = self.fixture.post("/api/scene/move",
                                  {"scene_id": scene_id, "direction": "down"})
        self.assertTrue(moved["ok"], moved)
        self.assertEqual(
            self.fixture.status_of("/api/scene/move", "POST",
                                   {"scene_id": scene_id, "direction": "sideways"}),
            400)

    def test_scene_update_in_demo(self):
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        saved = self.fixture.post("/api/scene/update", {
            "scene_id": scene_id, "name": "\u65b0\u540d\u5b57",
            "actions": [{"did": "demo-washer", "kind": "prop",
                          "siid": 2, "piid": 1, "value": False}]})
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(self.fixture.status_of("/api/scene/update", "POST",
                                                 {"name": "x"}), 400)
        self.assertEqual(
            self.fixture.status_of("/api/scene/update", "POST",
                                   {"scene_id": "nope"}),
            200)   # demo answers {ok: false} for an unknown id

    def test_scene_create_in_demo(self):
        result = self.fixture.post("/api/scene/create", {
            "did": "demo-washer", "name": "\u4e00\u952e\u5f00\u706f",
            "kind": "prop", "siid": 2, "piid": 1, "value": True})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fixture.status_of("/api/scene/create", "POST",
                                                 {"did": "demo-washer"}), 400)
        self.assertEqual(self.fixture.status_of("/api/scene/create", "POST",
                                                 {"name": "x"}), 400)

    def test_timer_create_in_demo(self):
        result = self.fixture.post("/api/timer/create", {
            "did": "demo-washer", "hour": 7, "minute": 30, "kind": "prop",
            "siid": 2, "piid": 1, "value": True, "name": "\u8d77\u5e8a"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fixture.status_of("/api/timer/create", "POST", {}), 400)
        self.assertEqual(
            self.fixture.status_of("/api/timer/create", "POST",
                                   {"did": "demo-washer", "hour": 7, "minute": 30,
                                    "kind": "weird", "siid": 2}),
            400)
        self.assertEqual(
            self.fixture.status_of("/api/timer/create", "POST",
                                   {"did": "demo-washer", "hour": 7, "minute": 30,
                                    "kind": "prop", "siid": 2,
                                    "repeat": "weekly"}),
            400,   # weekly without days
        )
        self.assertEqual(
            self.fixture.status_of("/api/timer/create", "POST",
                                   {"did": "demo-washer", "hour": 7, "minute": 30,
                                    "kind": "prop", "siid": 2,
                                    "repeat": "monthly"}),
            400,
        )

    def test_timer_update_and_delete_in_demo(self):
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        updated = self.fixture.post("/api/timer/update",
                                    {"scene_id": scene_id, "hour": 9, "minute": 5})
        self.assertTrue(updated["ok"], updated)
        deleted = self.fixture.post("/api/timer/delete", {"scene_id": scene_id})
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(self.fixture.status_of("/api/timer/update", "POST", {}), 400)
        self.assertEqual(self.fixture.status_of("/api/timer/delete", "POST", {}), 400)

    def test_timer_update_rejects_bad_time(self):
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        self.assertEqual(
            self.fixture.status_of("/api/timer/update", "POST",
                                   {"scene_id": scene_id, "hour": "x", "minute": 1}),
            400)

    def test_toggling_a_scene(self):
        scene_id = self.fixture.get("/api/scenes")["scenes"][0]["id"]
        result = self.fixture.post("/api/scene/status",
                                   {"scene_id": scene_id, "enable": False})
        self.assertTrue(result["ok"], result)
        refused = self.fixture.post("/api/scene/status", {"scene_id": "nope",
                                    "enable": True})
        self.assertFalse(refused["ok"])
        self.assertEqual(self.fixture.status_of("/api/scene/status", "POST", {}), 400)


class TestProviderSelection(unittest.TestCase):
    """``create_server(provider=None)`` must honour the credentials on disk.

    Regression (2026-11-21): a leftover ``provider = provider or DemoProvider()``
    turned the freshly-built cloud provider back into the demo one, so a
    logged-in account silently got demo data and the new auto plugin sync
    never ran.
    """

    def test_explicit_provider_is_used_as_is(self):
        sentinel = object()
        server = web_server.create_server(provider=sentinel, port=0)
        try:
            state = server.RequestHandlerClass.provider_state
            self.assertIs(state["provider"], sentinel)
            self.assertIsNone(state["stamp"], "explicit providers are never swapped")
        finally:
            server.server_close()

    def test_none_provider_keeps_what_make_provider_returned(self):
        sentinel = object()
        original = web_server.make_provider
        web_server.make_provider = lambda path=None: sentinel
        try:
            server = web_server.create_server(provider=None, port=0)
        finally:
            web_server.make_provider = original
        try:
            self.assertIs(server.RequestHandlerClass.provider_state["provider"],
                          sentinel)
        finally:
            server.server_close()


class LoginServer(unittest.TestCase):
    """A ``create_server(provider=None)`` instance with its own credentials file.

    This is the real path the desktop app takes: no explicit provider, so the
    server owns the provider and watches ``credentials.json``.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original_data_dir = web_server._data_dir
        self._original_sync = web_server.sync_plugins_in_background
        web_server._data_dir = lambda: self.tmp.name
        # switching to CloudProvider kicks off a plugin-text sync in a daemon
        # thread, which would call the real cloud with these fake tokens.
        # Unit tests must stay offline: the sync itself is covered elsewhere.
        web_server.sync_plugins_in_background = lambda provider: None
        self.credentials = os.path.join(self.tmp.name, "credentials.json")
        self.fixture = ServerFixture(server=web_server.create_server(
            provider=None, port=0, credentials_path=self.credentials)).start()

    def tearDown(self):
        self.fixture.stop()
        web_server.sync_plugins_in_background = self._original_sync
        web_server._data_dir = self._original_data_dir
        self.tmp.cleanup()

    def login(self):
        auth_mod.save_credentials(auth_mod.Credentials(
            user_id="1234567", c_user_id="c123", service_token="st-fake",
            ssecurity="ssec-fake", pass_token="pt-fake",
            device_id="abcdef0123456789"), self.credentials)


class TestLoginSwitchesProvider(LoginServer):
    """Issue #1 (2026-11-24): 新装用户扫码成功后仍然停在演示模式。

    ``stamp is None`` doubled as "no credentials file" and "never follow the
    file", so a fresh install's stamp was ``None`` and the running server kept
    the demo provider forever -- a restart (which re-read the file at startup)
    was the only way out.
    """

    def test_server_follows_the_credentials_file(self):
        state = self.fixture.server.RequestHandlerClass.provider_state
        self.assertTrue(state["follow"], "provider=None must follow the file")

    def test_first_login_leaves_demo_mode_without_a_restart(self):
        before = self.fixture.get("/api/status")
        self.assertTrue(before["demo"])
        self.assertFalse(before["credentials"])

        self.login()

        after = self.fixture.get("/api/status")
        self.assertFalse(after["demo"], "login must switch the provider")
        self.assertTrue(after["logged_in"])
        self.assertTrue(after["credentials"])
        self.assertEqual(after["provider"], "CloudProvider")

    def test_logout_returns_to_demo_and_deletes_the_file(self):
        self.login()
        self.assertFalse(self.fixture.get("/api/status")["demo"])

        result = self.fixture.post("/api/logout", {})
        self.assertTrue(result["ok"])
        self.assertTrue(result["demo"])
        self.assertFalse(os.path.exists(self.credentials))

        after = self.fixture.get("/api/status")
        self.assertTrue(after["demo"])
        self.assertFalse(after["credentials"])
        self.assertEqual(after["provider"], "DemoProvider")

    def test_logout_then_login_again_switches_back(self):
        self.login()
        self.fixture.post("/api/logout", {})
        self.assertTrue(self.fixture.get("/api/status")["demo"])

        self.login()
        self.assertFalse(self.fixture.get("/api/status")["demo"])

    def test_relogin_with_a_different_file_is_noticed(self):
        self.login()
        self.assertFalse(self.fixture.get("/api/status")["demo"])
        # a second account writes a shorter token; mtime could repeat on a
        # coarse filesystem, so the stamp carries the size as well
        auth_mod.save_credentials(auth_mod.Credentials(
            user_id="7654321", service_token="st2",
            ssecurity="s2", pass_token="pt2", device_id="dev2"), self.credentials)
        meta = self.fixture.get("/api/status")
        self.assertFalse(meta["demo"])
        self.assertEqual(meta["provider"], "CloudProvider")

    def test_logout_rejects_a_cross_site_post(self):
        self.login()
        code = self.fixture.status_of(
            "/api/logout", method="POST", payload={},
            headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertTrue(os.path.exists(self.credentials),
                        "a refused logout must not delete anything")


class TestBrokenCredentials(LoginServer):
    """A corrupt credentials file must not turn every request into a 500."""

    def test_corrupt_credentials_fall_back_to_demo_with_a_hint(self):
        with open(self.credentials, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        status = self.fixture.get("/api/status")
        self.assertTrue(status["demo"])
        self.assertTrue(status["credentials"])
        self.assertIn("凭据文件无法读取", status.get("credentials_error", ""))
        # the device list still answers instead of exploding
        self.assertEqual(self.fixture.status_of("/api/devices"), 200)

    def test_non_object_json_also_falls_back_to_demo(self):
        # valid JSON, but not an object: ``Credentials.from_dict`` raises
        # AttributeError, not ValueError, so the first fix missed this shape
        with open(self.credentials, "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")
        status = self.fixture.get("/api/status")
        self.assertTrue(status["demo"])
        self.assertIn("凭据文件无法读取", status.get("credentials_error", ""))

    def test_deleting_the_file_outside_the_app_also_returns_to_demo(self):
        self.login()
        self.assertFalse(self.fixture.get("/api/status")["demo"])
        os.remove(self.credentials)
        self.assertTrue(self.fixture.get("/api/status")["demo"])


class TestLoginFlowReset(unittest.TestCase):
    """退出登录必须把「已登录」状态和还没结束的扫码一起作废。"""

    def test_reset_clears_the_finished_state(self):
        flow = web_server.LoginFlow("unused.json")
        flow.state = "ok"
        flow.message = "logged in"
        flow.qr_png = b"png"
        flow.reset()
        self.assertEqual(flow.status(), {"state": "idle", "message": "",
                                         "has_image": False})

    def test_a_late_scan_result_cannot_write_credentials_back(self):
        """A scan that lands *after* logout must not recreate the file.

        This is the whole point of the generation counter, so exercise it
        instead of only asserting that ``reset()`` bumps the number.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "credentials.json")
            flow = web_server.LoginFlow(path)

            class FakeQr:
                def __init__(self, qr_size=400):
                    pass

                def request_qr(self):
                    return "http://127.0.0.1:1/qr", None, None

                def wait_for_scan(self, timeout=300):
                    flow.reset()      # the user logs out while the phone confirms
                    return auth_mod.Credentials(user_id="1", service_token="st")

            original_qr = auth_mod.QrLogin
            original_fetch = web_server._fetch_image
            auth_mod.QrLogin = FakeQr
            web_server._fetch_image = lambda login, url: b"png"
            try:
                flow._run(flow._generation)
            finally:
                auth_mod.QrLogin = original_qr
                web_server._fetch_image = original_fetch
            self.assertFalse(os.path.exists(path),
                             "a late scan must not restore the credentials file")
            self.assertEqual(flow.status()["state"], "idle")


class TestRemoteOnlyDevice(WebTestCase):
    """A TV has no generic controls: every value lives on the remote panel.

    Regression (2026-11-21): ``/api/device?did=<mitv>`` answered ``state: {}``
    and ``controls: []``.  The protocol was fine -- the provider dropped every
    remote-owned property from ``controls``, so nothing was ever polled.
    """

    def payload(self):
        return self.fixture.get("/api/device?did=demo-tv")

    def test_state_carries_volume_mute_and_input(self):
        payload = self.payload()
        self.assertEqual(payload["state"].get("4.1"), 12)
        self.assertEqual(payload["state"].get("4.2"), False)
        self.assertEqual(payload["state"].get("2.1"), 1)

    def test_remote_properties_are_bound_to_real_controls(self):
        payload = self.payload()
        controls = {(c["siid"], c["piid"]): c for c in payload["controls"]}
        for key in payload["remote"]["properties"].values():
            self.assertIn((key["siid"], key["piid"]), controls,
                          "the panel binds by siid/piid; the control must exist")

    def test_remote_values_are_not_repeated_in_the_generic_list(self):
        payload = self.payload()
        shown = [c["name"] for group in payload["groups"]
                 for c in group["writable"] + group["readonly"]]
        self.assertNotIn("volume", shown)
        self.assertNotIn("mute", shown)
        self.assertNotIn("volume", [c["name"] for c in payload["status_list"]])

    def test_remote_only_device_has_no_primary(self):
        # the home card must not offer an input-source picker as if it were a switch
        self.assertIsNone(self.payload()["primary"])


class StubCloudClient:
    """Just enough of CloudClient to exercise ``refresh_state`` offline."""

    base_url = "https://stub.invalid"

    def __init__(self, values):
        self.values = values
        self.calls = []

    def props(self, items, datasource=1):
        self.calls.append(list(items))
        return [{"did": did, "siid": siid, "piid": piid,
                 "value": self.values[(siid, piid)], "code": 0,
                 "updateTime": 1795153827}
                for did, siid, piid in items]


class TestCloudStateRefresh(unittest.TestCase):
    """The cloud path must poll a TV's remote-owned properties.

    This is the layer the bug lived in: ``refresh_state`` polls
    ``view.poll_items()``, so an empty control list means no request at all.
    """

    def setUp(self):
        with open(os.path.join(FIXTURES, "television_fsv1.json"), encoding="utf-8") as handle:
            parsed = spec_mod.parse_spec(json.load(handle))
        i18n_path = os.path.join(FIXTURES, "television_fsv1.i18n.json")
        if os.path.exists(i18n_path):
            with open(i18n_path, encoding="utf-8") as handle:
                spec_mod.apply_i18n(parsed, json.load(handle).get("data") or {})
        view = device_model.DeviceView("mitv.1", "xiaomi.tv.fsv1", "电视", False, parsed)
        self.client = StubCloudClient({(2, 1): 1, (4, 1): 12, (4, 2): False})
        self.provider = web_server.CloudProvider(None, client=self.client)
        self.provider._views["mitv.1"] = view

    def test_prop_get_is_actually_issued(self):
        self.provider.refresh_state("mitv.1")
        self.assertEqual(len(self.client.calls), 1, "prop/get must be called")
        self.assertIn(("mitv.1", 4, 1), self.client.calls[0])
        self.assertIn(("mitv.1", 2, 1), self.client.calls[0])

    def test_state_values_land(self):
        state = self.provider.refresh_state("mitv.1")
        self.assertEqual(state, {"2.1": 1, "4.1": 12, "4.2": False})

    def test_report_time_is_kept_for_the_ui(self):
        # offline devices answer with their last value; the UI must be able to
        # say how old it is instead of showing it as live
        self.provider.refresh_state("mitv.1")
        seen = self.provider.state_seen("mitv.1")
        self.assertEqual(seen.get("4.1"), 1795153827)
        self.assertEqual(set(seen), {"2.1", "4.1", "4.2"})


if __name__ == "__main__":
    unittest.main()

class TestDiagnosticsHeaders(WebTestCase):
    """失败要能对齐日志：响应头 X-Request-Id 与 JSON 里的 request_id 必须同值。

    用户弹框里看到的就是这两个值，日志里同一行也带 req=<同一个值>，三处对不上
    就等于没做请求追踪。
    """

    def _error_response(self, path, method="GET"):
        try:
            urlopen(self.fixture.url(path), timeout=10)
        except HTTPError as exc:
            return exc, json.loads(exc.read().decode("utf-8"))
        self.fail("expected an error status for %s" % path)

    def test_400_carries_request_id(self):
        exc, payload = self._error_response("/api/device")
        self.assertEqual(exc.code, 400)
        self.assertIn("error", payload)
        header = exc.headers.get("X-Request-Id")
        self.assertTrue(header, "响应头缺 X-Request-Id")
        self.assertEqual(header, payload.get("request_id"))
        self.assertRegex(header, r"^[0-9a-f]{8}$")

    def test_404_carries_request_id(self):
        exc, payload = self._error_response("/api/definitely-not-a-route")
        self.assertEqual(exc.code, 404)
        self.assertEqual(exc.headers.get("X-Request-Id"), payload.get("request_id"))

    def test_each_request_gets_a_fresh_id(self):
        first, _ = self._error_response("/api/device")
        second, _ = self._error_response("/api/device")
        self.assertNotEqual(first.headers.get("X-Request-Id"),
                            second.headers.get("X-Request-Id"))
