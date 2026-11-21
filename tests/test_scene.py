"""Scenes: payload normalisation and the provider wiring around it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import scene  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402


PAYLOAD = {
    "manual_scene_info_list": [
        {
            "scene_id": "1001",
            "name": "\u89c2\u5f71\u6a21\u5f0f",
            "enable": True,
            "tags": {"classification": "lighting"},
            "icon_url": "https://example.invalid/icon.png",
            "last_edit_time": "1764211073",
            "scene_action": {"actions": [
                {"payload_json": {"device_name": "\u732b\u7a9d\u706f\u7ec4",
                                   "did": "group.1", "model": "mijia.light.group3",
                                   "command": "set_properties", "delay_time": 0}},
                {"payload_json": {"device_name": "\u5ba2\u5385\u7a7a\u8c03",
                                   "did": "123", "command": "set_properties",
                                   "delay_time": 5}},
                {"payload_json": {"device_name": "\u732b\u7a9d\u706f\u7ec4",
                                   "did": "group.1", "command": "set_properties"}},
            ]},
        },
        {"scene_id": "1002", "name": "\u51fa\u95e8", "enable": False,
         "tags": {"classification": "something_new"}, "scene_action": None},
        {"name": "\u6ca1\u6709 id \u7684\u810f\u6570\u636e"},
    ],
    "auto_scene_info_list": [{"scene_id": "2001", "name": "\u56de\u5bb6",
                               "enable": True}],
    "voice_scene_info_list": [{}, {}],
}


class TestNormalise(unittest.TestCase):
    def setUp(self):
        self.data = scene.normalise(PAYLOAD, home={"id": "h1", "name": "\u5bb6"})

    def test_drops_records_without_id(self):
        self.assertEqual([s["id"] for s in self.data["scenes"]], ["1001", "1002"])

    def test_scene_fields(self):
        movie = self.data["scenes"][0]
        self.assertEqual(movie["name"], "\u89c2\u5f71\u6a21\u5f0f")
        self.assertTrue(movie["enabled"])
        self.assertEqual(movie["category_label"], "\u706f\u5149")
        self.assertEqual(movie["updated"], 1764211073)
        self.assertEqual(movie["icon"][:8], "https://")

    def test_devices_are_deduplicated_in_order(self):
        self.assertEqual(self.data["scenes"][0]["devices"],
                         ["\u732b\u7a9d\u706f\u7ec4", "\u5ba2\u5385\u7a7a\u8c03"])

    def test_delay_is_kept(self):
        actions = self.data["scenes"][0]["actions"]
        self.assertEqual(actions[1]["delay"], 5)

    def test_unknown_category_falls_through(self):
        self.assertEqual(self.data["scenes"][1]["category_label"], "something_new")

    def test_disabled_scene_without_actions(self):
        away = self.data["scenes"][1]
        self.assertFalse(away["enabled"])
        self.assertEqual(away["actions"], [])

    def test_automations_and_voice(self):
        self.assertEqual(self.data["automations"][0]["name"], "\u56de\u5bb6")
        self.assertEqual(self.data["voice_count"], 2)

    def test_automation_trigger_from_detail(self):
        detail = {
            "scene_trigger": {"triggers": [{
                "src": "timer", "name": "08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5(\u4e2d\u56fd\u5927\u9646)",
                "value_json": "0 45 8 * * * *",
                "extra_json": {"time_zone": "Asia/Shanghai"}}]},
            "scene_action": {"actions": [
                {"payload_json": {"device_name": "\u5c0f\u7c73\u5c0f\u7231\u97f3\u7bb1"}}]},
            "enable": False,
        }
        data = scene.normalise(PAYLOAD, details={"2001": detail})
        automation = data["automations"][0]
        self.assertEqual(automation["trigger"]["label"], "\u5b9a\u65f6")
        self.assertIn("08:45", automation["trigger"]["detail"])
        self.assertEqual(automation["trigger"]["cron"], "0 45 8 * * * *")
        self.assertEqual(automation["trigger"]["timezone"], "Asia/Shanghai")
        self.assertFalse(automation["enabled"], "detail wins over the list row")
        self.assertEqual(automation["devices"], ["\u5c0f\u7c73\u5c0f\u7231\u97f3\u7bb1"])

    def test_automation_without_detail_still_has_a_trigger(self):
        automation = self.data["automations"][0]
        self.assertIn("trigger", automation)
        self.assertEqual(automation["trigger"]["src"], "")
        self.assertEqual(automation["trigger"]["detail"], "")

    def test_empty_payload(self):
        data = scene.normalise(None)
        self.assertEqual(data["scenes"], [])
        self.assertEqual(data["automations"], [])


class StubClient:
    base_url = "https://stub.invalid"

    def __init__(self, run_code=0, status_code=True):
        self.homes_calls = 0
        self.status_calls = []
        self.info_calls = []
        self.status_code = status_code
        self.scene_calls = 0
        self.run_calls = []
        self.run_code = run_code

    def homes(self):
        self.homes_calls += 1
        return {"homelist": [{"id": "h1", "name": "\u5bb6",
                              "roomlist": []}]}

    def scenes(self, home_id=None):
        self.scene_calls += 1
        self.last_home = home_id
        return PAYLOAD

    def devices(self, home_id=None, start_did=None, limit=200):
        return {"list": [{"did": "dev-1", "model": "yeelink.light.mbulb3",
                          "name": "\u6ce1\u6ce1", "uid": "42",
                          "isOnline": True, "spec_type": "urn:x"}]}

    def scene_info(self, scene_id, home_id=None, owner_uid=None):
        self.info_calls.append((str(scene_id), home_id))
        return {
            "scene_id": str(scene_id),
            "name": "\u516b\u70b9\u56db\u5341\u4e94\u63d0\u9192",
            "scene_trigger": {"triggers": [{
                "src": "timer", "name": "08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5",
                "value_json": "0 45 8 * * * *"}]},
            "scene_action": {"mode": 0, "actions": [{
                "payload_json": {"command": "set_properties",
                                  "did": "dev-1", "value": []}}]},
            "enable": True,
        }

    def set_scene_status(self, scene_id, enable, home_id=None, owner_uid=None):
        self.status_calls.append((str(scene_id), bool(enable)))
        return self.status_code

    def scene_icons(self):
        self.icon_calls = getattr(self, "icon_calls", 0) + 1
        return [{"icon_id": 1, "icon_url": "https://cdn/a.png"},
                {"icon_id": 2, "icon_url": "https://cdn/b.png"},
                {"icon_id": 3, "icon_url": ""}]

    def scene_order(self, home_id, owner_uid):
        return getattr(self, "saved_order", {})

    def set_scene_order(self, home_id, room_id, owner_uid, scene_ids,
                        home_page=True):
        self.order_calls = getattr(self, "order_calls", [])
        self.order_calls.append((list(scene_ids), bool(home_page)))
        return True

    def edit_scene(self, payload):
        self.edit_payload = payload
        return {"scene_id": payload.get("scene_id") or "new-1"}

    def delete_scene(self, scene_id, home_id=None, owner_uid=None, del_from=1):
        self.deleted = (str(scene_id), del_from)
        return True

    def run_scene(self, scene_id, home_id=None, scene_type=2, phone_id="",
                  owner_uid=None):
        self.run_calls.append((str(scene_id), home_id, int(scene_type), phone_id,
                               owner_uid))
        if self.run_code is True:
            return True                    # the gateway's real success shape
        return {"code": self.run_code}


class TestCloudProviderScenes(unittest.TestCase):
    def setUp(self):
        self.client = StubClient()
        self.provider = web_server.CloudProvider(None, client=self.client)

    def test_home_and_scenes(self):
        data = self.provider.scenes()
        self.assertEqual(data["home"]["id"], "h1")
        self.assertEqual(len(data["scenes"]), 2)
        self.assertEqual(self.client.last_home, "h1")

    def test_scenes_are_cached(self):
        self.provider.scenes()
        self.provider.scenes()
        self.assertEqual(self.client.scene_calls, 1)

    def test_run_scene_success(self):
        result = self.provider.run_scene("1001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], 0)
        scene_id, home_id, scene_type, phone_id, owner_uid = self.client.run_calls[0]
        self.assertEqual(scene_id, "1001")
        self.assertEqual(home_id, "h1")
        self.assertEqual(scene_type, 2, "manual scenes are SPEC_SCENE_TYPE")
        self.assertTrue(phone_id)

    def test_true_payload_counts_as_success(self):
        # the live gateway answers ``true`` for NewRunScene, not {code: 0}
        provider = web_server.CloudProvider(None, client=StubClient(run_code=True))
        result = provider.run_scene("1001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], 0)

    def test_automations_get_trigger_details(self):
        data = self.provider.scenes()
        automation = data["automations"][0]
        self.assertEqual(automation["trigger"]["label"], "\u5b9a\u65f6")
        self.assertEqual(automation["trigger"]["cron"], "0 45 8 * * * *")
        self.assertEqual(self.client.info_calls[0][0], "2001")

    def test_set_scene_status_true_is_success(self):
        result = self.provider.set_scene_status("2001", False)
        self.assertTrue(result["ok"])
        self.assertEqual(self.client.status_calls, [("2001", False)])

    def test_set_scene_status_rejection(self):
        provider = web_server.CloudProvider(None, client=StubClient(status_code=False))
        result = provider.set_scene_status("2001", True)
        self.assertFalse(result["ok"])
        self.assertIn("code", result["message"])

    def test_update_timer_rewrites_the_clock(self):
        result = self.provider.update_timer("2001", hour=9, minute=5)
        self.assertTrue(result["ok"])
        payload = self.client.edit_payload
        trigger = payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["value_json"], "0 5 9 * * * *")
        self.assertEqual(payload["scene_id"], "2001")
        self.assertEqual(payload["home_id"], "h1")

    def test_update_timer_failure_is_reported(self):
        class Broken(StubClient):
            def scene_info(self, *args, **kwargs):
                raise RuntimeError("boom")

        provider = web_server.CloudProvider(None, client=Broken())
        result = provider.update_timer("2001", hour=9, minute=5)
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["message"])

    def test_create_timer_uses_a_real_action_template(self):
        result = self.provider.create_timer("dev-1", 7, 30, name="\u8d77\u5e8a",
                                            kind="prop", siid=2, piid=1,
                                            value=True)
        self.assertTrue(result["ok"], result)
        payload = self.client.edit_payload
        self.assertNotIn("scene_id", payload)
        self.assertEqual(payload["name"], "\u8d77\u5e8a")
        action = payload["scene_action"]["actions"][0]
        self.assertEqual(action["payload_json"]["did"], "dev-1")
        self.assertEqual(action["payload_json"]["value"][0]["value"], True)
        self.assertEqual(
            payload["scene_trigger"]["triggers"][0]["value_json"],
            "0 30 7 * * * *")

    def test_create_scene_uses_the_click_trigger(self):
        result = self.provider.create_scene("\u4e00\u952e\u5173\u706f", "dev-1",
                                            kind="prop", siid=2, piid=1,
                                            value=False)
        self.assertTrue(result["ok"], result)
        payload = self.client.edit_payload
        self.assertNotIn("scene_id", payload)
        self.assertEqual(payload["name"], "\u4e00\u952e\u5173\u706f")
        trigger = payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["key"], "user.click")
        action = payload["scene_action"]["actions"][0]
        self.assertEqual(action["payload_json"]["value"][0]["value"], False)

    def test_create_scene_with_several_actions(self):
        result = self.provider.create_scene("\u4e24\u4e2a\u52a8\u4f5c", actions=[
            {"did": "dev-1", "kind": "prop", "siid": 2, "piid": 1,
             "value": False},
            {"did": "dev-1", "kind": "prop", "siid": 2, "piid": 1,
             "value": True},
        ])
        self.assertTrue(result["ok"], result)
        actions = self.client.edit_payload["scene_action"]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertEqual([item["order"] for item in actions], [1, 2])
        self.assertEqual([item["id"] for item in actions], [1, 2])
        values = [item["payload_json"]["value"][0]["value"] for item in actions]
        self.assertEqual(values, [False, True])

    def test_create_scene_rejects_an_unknown_action_device(self):
        result = self.provider.create_scene("x", actions=[
            {"did": "dev-1", "kind": "prop", "siid": 2, "piid": 1,
             "value": True},
            {"did": "nope", "kind": "prop", "siid": 2, "piid": 1,
             "value": True},
        ])
        self.assertFalse(result["ok"])

    def test_clone_automation_keeps_the_trigger(self):
        result = self.provider.clone_automation("2001", "\u526f\u672c",
                                                actions=[{"did": "dev-1",
                                                          "kind": "prop",
                                                          "siid": 2, "piid": 1,
                                                          "value": True}])
        self.assertTrue(result["ok"], result)
        payload = self.client.edit_payload
        self.assertNotIn("scene_id", payload)
        self.assertEqual(payload["name"], "\u526f\u672c")
        self.assertEqual(
            payload["scene_trigger"]["triggers"][0]["value_json"],
            "0 45 8 * * * *", "the source trigger is copied verbatim")

    def test_reorder_scenes_writes_the_new_order(self):
        self.client.saved_order = {"home_page_display_ids": [],
                                    "second_page_display_ids": ["1001", "1002"]}
        result = self.provider.reorder_scenes(["1002", "1001"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.client.order_calls[-1], (["1002", "1001"], False))

    def test_reorder_scenes_rejects_unknown_ids(self):
        self.client.saved_order = {"home_page_display_ids": [],
                                    "second_page_display_ids": ["1001"]}
        result = self.provider.reorder_scenes(["nope", "1001"])
        self.assertFalse(result["ok"])
        self.assertIn("nope", result["message"])
        self.assertFalse(getattr(self.client, "order_calls", []))

    def test_reorder_scenes_is_a_noop_when_unchanged(self):
        self.client.saved_order = {"home_page_display_ids": [],
                                    "second_page_display_ids": ["1001", "1002"]}
        self.assertTrue(self.provider.reorder_scenes(["1001", "1002"])["ok"])
        self.assertFalse(getattr(self.client, "order_calls", []))

    def test_scene_icons_are_cached_and_filtered(self):
        data = self.provider.scene_icons()
        self.assertEqual([item["id"] for item in data["icons"]], [1, 2])
        self.provider.scene_icons()
        self.assertEqual(self.client.icon_calls, 1, "catalogue is cached")

    def test_set_scene_icon_sends_the_url(self):
        result = self.provider.set_scene_icon("2001", "https://cdn/b.png")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.client.edit_payload["icon_url"],
                         "https://cdn/b.png")
        self.assertEqual(self.client.edit_payload["scene_id"], "2001")

    def test_update_scene_can_change_the_icon(self):
        self.provider.update_scene("2001", name="x", icon_url="https://cdn/a.png")
        self.assertEqual(self.client.edit_payload["icon_url"],
                         "https://cdn/a.png")

    def test_create_scene_can_set_an_icon(self):
        self.provider.create_scene("x", actions=[{"did": "dev-1",
                                                    "kind": "prop",
                                                    "siid": 2, "piid": 1,
                                                    "value": True}],
                                   icon_url="https://cdn/a.png")
        self.assertEqual(self.client.edit_payload["icon_url"],
                         "https://cdn/a.png")

    def test_scenes_follow_the_saved_order(self):
        self.client.saved_order = {"home_page_display_ids": ["1002"],
                                    "second_page_display_ids": ["1001"]}
        data = self.provider.refresh_scenes()
        self.assertEqual([scene["id"] for scene in data["scenes"]],
                         ["1002", "1001"])

    def test_move_scene_writes_the_swapped_order(self):
        self.client.saved_order = {"home_page_display_ids": [],
                                    "second_page_display_ids": ["1001", "1002"]}
        result = self.provider.move_scene("1001", "down")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.client.order_calls[-1],
                         (["1002", "1001"], False),
                         "full order goes to the second-page list")

    def test_move_scene_at_the_edge_is_a_noop(self):
        self.client.saved_order = {"home_page_display_ids": [],
                                    "second_page_display_ids": ["1001", "1002"]}
        self.assertTrue(self.provider.move_scene("1001", "up")["ok"])
        self.assertFalse(getattr(self.client, "order_calls", []))

    def test_timer_trigger_carries_the_next_run(self):
        trigger = scene._trigger({"src": "timer",
                                 "value_json": "0 45 8 * * * *",
                                 "extra_json": {"filter": "cn_workday"}})
        self.assertTrue(trigger["next_run"])
        self.assertIn("T08:45:00", trigger["next_run"])
        self.assertTrue(trigger["next_run_approx"],
                        "workday has no holiday data, must be marked approximate")

    def test_plain_daily_timer_is_exact(self):
        trigger = scene._trigger({"src": "timer",
                                 "value_json": "0 45 8 * * * *"})
        self.assertEqual(trigger["next_run_status"], "exact")
        self.assertFalse(trigger["next_run_approx"])

    def test_workday_is_marked_approximate(self):
        trigger = scene._trigger({"src": "timer",
                                 "value_json": "0 45 8 * * * *",
                                 "extra_json": {"filter": "cn_workday"}})
        self.assertEqual(trigger["next_run_status"], "approximate_weekday")
        self.assertTrue(trigger["next_run"])
        self.assertTrue(trigger["next_run_approx"])

    def test_holiday_has_no_local_next_run(self):
        # 法定节假日由云端按日历筛选，本地算不出来就不给日期
        trigger = scene._trigger({"src": "timer",
                                 "value_json": "0 45 8 * * * *",
                                 "extra_json": {"filter": "cn_freeday"}})
        self.assertEqual(trigger["next_run_status"], "unavailable_holiday")
        self.assertEqual(trigger["next_run"], "")

    def test_update_scene_renames_and_rearms(self):
        result = self.provider.update_scene("2001", name="\u65b0\u540d\u5b57",
                                            actions=[{"did": "dev-1",
                                                      "kind": "prop",
                                                      "siid": 2, "piid": 1,
                                                      "value": False}])
        self.assertTrue(result["ok"], result)
        payload = self.client.edit_payload
        self.assertEqual(payload["scene_id"], "2001",
                         "editing must keep the id")
        self.assertEqual(payload["name"], "\u65b0\u540d\u5b57")
        self.assertEqual(
            payload["scene_action"]["actions"][0]["payload_json"]["value"][0]["value"],
            False)

    def test_create_scene_needs_a_device(self):
        result = self.provider.create_scene("x", "nope", siid=2, piid=1)
        self.assertFalse(result["ok"])

    def test_create_weekly_timer(self):
        result = self.provider.create_timer("dev-1", 7, 30, name="\u4e0a\u73ed",
                                            kind="prop", siid=2, piid=1,
                                            value=True, repeat="weekly",
                                            days=[1, 3])
        self.assertTrue(result["ok"], result)
        trigger = self.client.edit_payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["value_json"], "0 30 7 * * 1,3 *")
        self.assertIn("\u5468\u4e00", trigger["name"])
        self.assertEqual(trigger["extra_json"]["filter"], "")

    def test_create_workday_timer_sets_filter(self):
        self.provider.create_timer("dev-1", 7, 30, kind="prop", siid=2, piid=1,
                                   value=True, repeat="workday")
        trigger = self.client.edit_payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["extra_json"]["filter"], "cn_workday")
        self.assertEqual(trigger["value_json"], "0 30 7 * * * *")

    def test_create_timer_needs_a_device(self):
        result = self.provider.create_timer("nope", 7, 30, siid=2, piid=1)
        self.assertFalse(result["ok"])

    def test_delete_timer(self):
        result = self.provider.delete_timer("2001")
        self.assertTrue(result["ok"])
        self.assertEqual(self.client.deleted, ("2001", 1))

    def test_run_scene_failure_maps_code(self):
        provider = web_server.CloudProvider(None, client=StubClient(run_code=-3))
        result = provider.run_scene("1001")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], -3)
        self.assertIn("-3", result["message"])


if __name__ == "__main__":
    unittest.main()
