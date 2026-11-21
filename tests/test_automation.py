"""Creating a timer: action retargeting and the create payload."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import automation  # noqa: E402

TEMPLATE_ACTION = {
    "group_id": 0, "id": 1, "order": 1, "type": 0,
    "name": "\u6253\u5f00\u706f", "payload": "",
    "payload_json": {
        "command": "set_properties", "delay_time": 0,
        "device_name": "\u706f\u4e00\u7ec4", "did": "group.1",
        "model": "mijia.light.group3", "uid": "42",
        "value": [{"did": "group.1", "piid": 1, "siid": 2, "value": True}],
    },
    "protocol_type": 2, "sa_id": 99, "from": 0, "device_group_id": 0,
    "nested_scene_info": None, "std_sa_id": "", "option_ids": [],
    "data_ver": 0, "need_fill": 0,
}

TEMPLATE_SCENE = {
    "scene_id": "111", "name": "\u6a21\u677f", "uid": "42",
    "home_id": "h1", "enable": True, "type": 0, "version": 2,
    "create_time": "1", "last_edit_time": "2",
    "icon_url": "https://x/y.png",
    "scene_trigger": {"triggers": [{"src": "timer"}]},
    "scene_condition": {"express": 0, "conditions": []},
    "scene_action": {"mode": 0, "actions": [TEMPLATE_ACTION]},
    "scene_else_action": {"mode": 0, "actions": []},
    "temporary": False, "tags": {},
}


class TestActionFrom(unittest.TestCase):
    def test_retargets_property_write(self):
        action = automation.build_action_from(
            TEMPLATE_ACTION, "set_properties", "did-new", 2, piid=1,
            value=False, device_name="\u6ce1\u6ce1", model="light", uid="7")
        payload = action["payload_json"]
        self.assertEqual(payload["did"], "did-new")
        self.assertEqual(payload["device_name"], "\u6ce1\u6ce1")
        self.assertEqual(payload["uid"], "7")
        self.assertEqual(payload["value"],
                         [{"did": "did-new", "piid": 1, "siid": 2, "value": False}])

    def test_keeps_the_envelope(self):
        action = automation.build_action_from(TEMPLATE_ACTION, "set_properties",
                                              "d", 2, piid=1, value=True)
        self.assertEqual(action["sa_id"], 99, "the phone's bookkeeping is kept")
        self.assertEqual(action["name"], "\u6253\u5f00\u706f")
        self.assertEqual(action["order"], 1)

    def test_action_command_shape(self):
        template = {"payload_json": {"command": "action", "value": {}}}  # noqa: C408
        action = automation.build_action_from(template, "action", "tv", 5,
                                              aiid=4, value=["x"])
        self.assertEqual(action["payload_json"]["value"],
                         {"aiid": 4, "did": "tv", "siid": 5, "in": ["x"]})

    def test_template_is_not_mutated(self):
        automation.build_action_from(TEMPLATE_ACTION, "set_properties", "d", 2,
                                     piid=1, value=False)
        self.assertEqual(TEMPLATE_ACTION["payload_json"]["did"], "group.1")

    def test_rejects_bad_template(self):
        with self.assertRaises(ValueError):
            automation.build_action_from({}, "set_properties", "d", 1, piid=1)
        with self.assertRaises(ValueError):
            automation.build_action_from({"payload_json": None}, "set_properties",
                                         "d", 1, piid=1)


class TestCreatePayload(unittest.TestCase):
    def payload(self, **kwargs):
        options = {"name": "\u65b0\u5b9a\u65f6", "hour": 9, "minute": 5,
                   "actions": [TEMPLATE_ACTION], "home_id": "h1",
                   "owner_uid": "42"}
        options.update(kwargs)
        return automation.build_payload(TEMPLATE_SCENE, **options)

    def test_drops_scene_id_so_edit_creates(self):
        payload = self.payload()
        self.assertNotIn("scene_id", payload)
        for field in ("uid", "create_time", "last_edit_time", "icon_url"):
            self.assertNotIn(field, payload)

    def test_sets_timer_trigger(self):
        payload = self.payload()
        trigger = payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["src"], "timer")
        self.assertEqual(trigger["value_json"], "0 5 9 * * * *")
        self.assertEqual(trigger["name"], "09:05 \u6bcf\u5929")
        self.assertEqual(trigger["extra_json"]["time_zone"], "Asia/Shanghai")

    def test_replaces_actions_and_name(self):
        action = dict(TEMPLATE_ACTION)   # noqa: C408 - a copy, not a literal
        action["order"] = 1
        payload = self.payload(actions=[action], name="\u8d77\u5e8a\u5f00\u706f")
        self.assertEqual(payload["name"], "\u8d77\u5e8a\u5f00\u706f")
        self.assertEqual(len(payload["scene_action"]["actions"]), 1)
        self.assertEqual(payload["scene_condition"]["conditions"], [])
        self.assertEqual(payload["scene_else_action"]["actions"], [])

    def test_bookkeeping_fields(self):
        payload = self.payload()
        self.assertEqual(payload["edit_from"], 1)
        self.assertEqual(payload["value_format"], 1)
        self.assertEqual(payload["app_version"], 19)
        self.assertEqual(payload["owner_uid"], "42")
        self.assertTrue(payload["enable"])

    def test_rejects_missing_inputs(self):
        with self.assertRaises(ValueError):
            automation.build_payload({}, "x", 1, 1, [TEMPLATE_ACTION])
        with self.assertRaises(ValueError):
            self.payload(actions=[])
        with self.assertRaises(ValueError):
            self.payload(name="   ")


class TestScenePayload(unittest.TestCase):
    def test_trigger_is_user_click(self):
        payload = automation.build_scene_payload(
            TEMPLATE_SCENE, "\u4e00\u952e\u5173\u706f", [TEMPLATE_ACTION],
            home_id="h1", owner_uid="42")
        trigger = payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["src"], "user")
        self.assertEqual(trigger["key"], "user.click")
        self.assertEqual(trigger["sc_id"], 1)

    def test_replaces_the_timer_trigger(self):
        payload = automation.build_scene_payload(TEMPLATE_SCENE, "x",
                                                [TEMPLATE_ACTION])
        self.assertEqual(len(payload["scene_trigger"]["triggers"]), 1)
        self.assertIsNone(payload["scene_trigger"]["triggers"][0]["value_json"])

    def test_drops_scene_id_and_read_only_fields(self):
        payload = automation.build_scene_payload(TEMPLATE_SCENE, "x",
                                                [TEMPLATE_ACTION])
        for field in ("scene_id", "uid", "create_time", "last_edit_time",
                      "icon_url"):
            self.assertNotIn(field, payload)

    def test_user_trigger_is_a_copy(self):
        first = automation.user_trigger()
        first["sc_id"] = 99
        self.assertEqual(automation.user_trigger()["sc_id"], 1)

    def test_rejects_empty_actions(self):
        with self.assertRaises(ValueError):
            automation.build_scene_payload(TEMPLATE_SCENE, "x", [])


class TestUpdatePayload(unittest.TestCase):
    def test_keeps_the_id_and_replaces_only_what_is_given(self):
        payload = automation.build_update_payload(
            TEMPLATE_SCENE, name="\u65b0\u540d\u5b57", home_id="h1",
            owner_uid="42")
        self.assertEqual(payload["scene_id"], "111")
        self.assertEqual(payload["name"], "\u65b0\u540d\u5b57")
        self.assertEqual(payload["scene_action"]["actions"],
                         TEMPLATE_SCENE["scene_action"]["actions"])
        self.assertEqual(payload["edit_from"], 1)
        self.assertEqual(payload["app_version"], 19)

    def test_replaces_actions(self):
        payload = automation.build_update_payload(TEMPLATE_SCENE,
                                                  actions=[TEMPLATE_ACTION])
        self.assertEqual(len(payload["scene_action"]["actions"]), 1)
        self.assertEqual(payload["name"], "\u6a21\u677f")

    def test_needs_a_scene_id(self):
        with self.assertRaises(ValueError):
            automation.build_update_payload({"name": "x"}, name="y")
        with self.assertRaises(ValueError):
            automation.build_update_payload(TEMPLATE_SCENE, name="  ")

    def test_does_not_mutate_the_record(self):
        automation.build_update_payload(TEMPLATE_SCENE, name="x")
        self.assertEqual(TEMPLATE_SCENE["name"], "\u6a21\u677f")


class TestClonePayload(unittest.TestCase):
    def test_drops_the_id_but_keeps_the_trigger(self):
        TEMPLATE_SCENE["scene_trigger"] = {"express": 1, "triggers": [
            {"src": "device", "key": "prop.2.1078", "value_json": 1}]}
        payload = automation.build_clone_payload(TEMPLATE_SCENE, "\u526f\u672c")
        self.assertNotIn("scene_id", payload)
        self.assertEqual(payload["name"], "\u526f\u672c")
        self.assertEqual(payload["scene_trigger"]["triggers"][0]["key"],
                         "prop.2.1078")

    def test_can_replace_actions(self):
        payload = automation.build_clone_payload(TEMPLATE_SCENE, "x",
                                                actions=[TEMPLATE_ACTION])
        self.assertEqual(len(payload["scene_action"]["actions"]), 1)
        self.assertNotIn("scene_id", payload)

    def test_needs_an_id_to_copy(self):
        with self.assertRaises(ValueError):
            automation.build_clone_payload({"name": "x"}, "y")


if __name__ == "__main__":
    unittest.main()
