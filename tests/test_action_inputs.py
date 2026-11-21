"""Action arguments: inline params must never be sent as None."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mihome_pc.domain import model as device_model  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402
from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

# 合成 spec：一个服务里放四个动作，覆盖无参/引用/inline/混合
SPEC = {
    "type": "urn:miot-spec-v2:device:test:0000A000:test:1",
    "description": "test",
    "services": [
        {"iid": 2, "type": "urn:miot-spec-v2:service:test:00007800:test:1",
         "description": "Test",
         "properties": [
             {"iid": 1, "type": "urn:miot-spec-v2:property:mode:00000008:test:1",
              "description": "Mode", "format": "uint8", "access": ["read", "write", "notify"],
              "value-list": [{"value": 0, "description": "A"},
                                  {"value": 1, "description": "B"}]},
             {"iid": 2, "type": "urn:miot-spec-v2:property:temperature:00000020:test:1",
              "description": "Temp", "format": "uint8", "access": ["read", "notify"],
              "value-range": [30, 90, 1]},
         ],
         "actions": [
             {"iid": 1, "type": "urn:miot-spec-v2:action:noop:00002800:test:1",
              "description": "Noop", "in": [], "out": []},
             {"iid": 2, "type": "urn:miot-spec-v2:action:withref:00002801:test:1",
              "description": "WithRef", "in": [1], "out": []},
             {"iid": 3, "type": "urn:miot-spec-v2:action:withinline:00002802:test:1",
              "description": "WithInline",
              "in": [{"piid": 1, "type": "urn:miot-spec-v2:property:level:00000008:test:1",
                       "description": "Level", "format": "uint8",
                       "value-range": [1, 100, 1]}], "out": []},
             {"iid": 4, "type": "urn:miot-spec-v2:action:mixed:00002803:test:1",
              "description": "Mixed",
              "in": [1,
                     {"piid": 1, "type": "urn:miot-spec-v2:property:level:00000008:test:1",
                      "description": "Level", "format": "uint8",
                      "value-range": [1, 100, 1]}], "out": []},
             {"iid": 5, "type": "urn:miot-spec-v2:action:withbool:00002804:test:1",
              "description": "WithBool",
              "in": [{"piid": 1, "type": "urn:miot-spec-v2:property:on:00000006:test:1",
                       "description": "On", "format": "bool"}], "out": []},
             {"iid": 6, "type": "urn:miot-spec-v2:action:withenum:00002805:test:1",
              "description": "WithEnum",
              "in": [{"piid": 1, "type": "urn:miot-spec-v2:property:mode:00000008:test:1",
                       "description": "Mode", "format": "uint8",
                       "value-list": [{"value": 0, "description": "A"},
                                        {"value": 1, "description": "B"}]}], "out": []},
             {"iid": 7, "type": "urn:miot-spec-v2:action:withtext:00002806:test:1",
              "description": "WithText",
              "in": [{"piid": 1, "type": "urn:miot-spec-v2:property:name:00000001:test:1",
                       "description": "Name", "format": "string"}], "out": []},
         ]},
    ],
}


class CountingClient:
    def __init__(self):
        self.calls = []

    def edit_scene(self, payload):
        self.calls.append(("edit_scene", payload.get("name")))
        return {"result": {"code": 0, "scene_id": "s1"}}

    def devices(self):
        return {"list": [{"did": "d1", "model": "test.model",
                           "name": "dev", "uid": "1", "isOnline": True}]}

    def call_action(self, did, siid, aiid, args):
        self.calls.append(list(args))
        # 真实路径下 CloudClient.post 已把外层 result 拆掉，剩下 {"code": 0}
        return {"code": 0}


class _Creds:
    user_id = "1"
    c_user_id = "1"
    service_token = "t"
    ssecurity = "s"
    pass_token = "p"
    device_id = "d"


def make_provider(state=None, view=True):
    client = CountingClient()
    provider = web_server.CloudProvider(_Creds(), client=client)
    provider._home_id = lambda: "100"
    provider._home_uid = "1"
    if not view:
        def boom(_did):
            raise RuntimeError("spec unavailable")
        provider.view = boom
        return provider, client
    spec = spec_mod.parse_spec(SPEC)
    view = device_model.DeviceView("d1", "test.model", "dev", True, spec)
    provider._views["d1"] = view
    provider._states["d1"] = dict(state or {(2, 1): 1, (2, 2): 55})
    return provider, client


class TestActionInputs(unittest.TestCase):
    def test_no_argument_action_sends_empty_list(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.calls, [[]])

    def test_reference_action_uses_current_value(self):
        provider, client = make_provider({(2, 1): 1})
        result = provider.run_action("d1", 2, 2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.calls, [[1]])

    def test_inline_action_uses_the_given_value(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 3, inputs={"1": 42})
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.calls, [[42]])

    def test_mixed_action_keeps_spec_order(self):
        provider, client = make_provider({(2, 1): 1})
        result = provider.run_action("d1", 2, 4, inputs={"1": 7})
        self.assertTrue(result["ok"], result)
        self.assertEqual(client.calls, [[1, 7]], "引用在前，inline 在后")

    def test_missing_inline_value_never_calls_cloud(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 3)
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_out_of_range_value_never_calls_cloud(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 3, inputs={"1": 500})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_bad_type_never_calls_cloud(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 3, inputs={"1": "abc"})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_unknown_action_never_calls_cloud(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 9, 9)
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_bool_string_must_be_explicit(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 5, inputs={"1": "not-a-bool"})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_integer_rejects_fractional(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 3, inputs={"1": 1.5})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [], "1.5 不能被截成 1")

    def test_enum_rejects_fractional(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 6, inputs={"1": 1.5})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_string_param_rejects_non_text(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 7, inputs={"1": [1, 2]})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_integer_rejects_bool(self):
        provider, client = make_provider()
        result = provider.run_action("d1", 2, 3, inputs={"1": True})
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_scene_editor_rejects_parameter_actions(self):
        provider, client = make_provider()
        actions, error = provider._build_actions([
            {"did": "d1", "kind": "action", "siid": 2, "aiid": 3,
             "inputs": []}])
        self.assertIsNone(actions)
        self.assertIn("参数", error or "")

    def test_create_scene_fails_closed_when_spec_unavailable(self):
        provider, client = make_provider(view=False)
        result = provider.create_scene("x", did="d1", kind="action",
                                       siid=2, aiid=1, inputs=[])
        self.assertFalse(result["ok"], result)
        self.assertEqual([c for c in client.calls if isinstance(c, tuple)], [],
                         "spec 拿不到时不能写云端")

    def test_create_scene_rejects_unknown_action(self):
        provider, client = make_provider()
        result = provider.create_scene("x", did="d1", kind="action",
                                       siid=2, aiid=99, inputs=[])
        self.assertFalse(result["ok"], result)
        self.assertIn("没有这个动作", result["message"])
        self.assertEqual([c for c in client.calls if isinstance(c, tuple)], [])

    def test_create_scene_rejects_parameter_action(self):
        provider, client = make_provider()
        result = provider.create_scene("x", did="d1", kind="action",
                                       siid=2, aiid=3, inputs=[1])
        self.assertFalse(result["ok"], result)
        self.assertIn("参数", result["message"])
        self.assertEqual([c for c in client.calls if isinstance(c, tuple)], [],
                         "带参数动作不能写云端")

    def test_create_scene_succeeds_for_parameterless_action(self):
        provider, client = make_provider()
        provider._scene_template = lambda want_action=False: (
            {"scene_id": "old"}, {"payload_json": {"command": "action"}}, "")
        original = web_server.automation_mod.build_scene_payload

        def fake_payload(*args, **kwargs):
            return {"name": "x", "actions": []}

        web_server.automation_mod.build_scene_payload = fake_payload
        try:
            result = provider.create_scene("x", did="d1", kind="action",
                                           siid=2, aiid=1, inputs=[])
        finally:
            web_server.automation_mod.build_scene_payload = original
        self.assertTrue(result["ok"], result)
        edits = [c for c in client.calls if isinstance(c, tuple)
                 and c[0] == "edit_scene"]
        self.assertEqual(len(edits), 1, "无参数动作应该照常创建")

    def test_parameterless_action_is_allowed_by_the_guard(self):
        # 回归保护：无参数动作不能被误拦
        provider, _client = make_provider()
        self.assertEqual(
            provider._action_params_reason("d1", {"siid": 2, "aiid": 1}), "")

    def test_control_exposes_params_schema(self):
        spec = spec_mod.parse_spec(SPEC)
        view = device_model.DeviceView("d1", "test.model", "dev", True, spec)
        action = [a for a in view.actions if a.aiid == 4][0]
        payload = action.to_dict()
        self.assertEqual(len(payload["params"]), 2)
        self.assertEqual(payload["in_refs"], [1],
                         "inline 参数不能混进引用列表")
        self.assertFalse(payload["params"][0]["inline"])
        self.assertTrue(payload["params"][1]["inline"])
        self.assertEqual(payload["params"][1]["value_range"], [1, 100, 1])


if __name__ == "__main__":
    unittest.main()
