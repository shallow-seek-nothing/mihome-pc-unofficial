"""CloudProvider must reject bad writes **before** touching the cloud."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mihome_pc.domain import model as device_model  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402
from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "specs")


def load_spec(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return spec_mod.parse_spec(json.load(handle))


class CountingClient:
    """记录每次写调用，并返回可配置的结果。"""

    def __init__(self, accept=True):
        self.calls = []
        self.accept = accept

    def set_props(self, writes):
        self.calls.append([tuple(w) for w in writes])
        code = 0 if self.accept else -704010000
        # 真实回包形状：外层 result 已被 CloudClient.post 拆掉，
        # 剩下 {"params": [{did,siid,piid,code}, ...]}
        return {"params": [{"did": w[0], "siid": w[1], "piid": w[2],
                              "code": code} for w in writes]}

    def call_action(self, did, siid, aiid, args):
        self.calls.append(("action", did, siid, aiid, list(args)))
        return {"result": {"code": 0}}


def make_provider(spec_name, model_name, client, state=None):
    provider = web_server.CloudProvider(None, client=client)
    view = device_model.DeviceView("d1", model_name, "dev", True,
                                   load_spec(spec_name))
    provider._views["d1"] = view
    provider._states["d1"] = dict(state or {})
    return provider


class TestWriteValidation(unittest.TestCase):
    def test_unknown_property_never_reaches_cloud(self):
        client = CountingClient()
        provider = make_provider("fryer_maf04.json", "careli.fryer.maf04", client)
        result = provider.set_property("d1", 9, 9, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [], "云端不应被调用")

    def test_limit_rejection_never_reaches_cloud(self):
        # 空气炸锅：2.4 = 目标温度，2.3 = 烹饪时长；180°C 下 600 分钟超出测量上限
        client = CountingClient()
        provider = make_provider("fryer_maf04.json", "careli.fryer.maf04", client,
                                 state={(2, 4): 180})
        result = provider.set_property("d1", 2, 3, 600)
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])
        self.assertTrue(result["message"])

    def test_read_only_property_never_reaches_cloud(self):
        client = CountingClient()
        provider = make_provider("fryer_maf04.json", "careli.fryer.maf04", client)
        result = provider.set_property("d1", 2, 1, 3)   # 2.1 status 只读
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_bad_value_never_reaches_cloud(self):
        client = CountingClient()
        provider = make_provider("fryer_maf04.json", "careli.fryer.maf04", client)
        result = provider.set_property("d1", 2, 4, "not-a-number")
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_good_value_calls_cloud_once_with_coerced_value(self):
        client = CountingClient()
        provider = make_provider("fryer_maf04.json", "careli.fryer.maf04", client)
        result = provider.set_property("d1", 2, 4, "200")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0][3], 200)
        self.assertEqual(provider._states["d1"][(2, 4)], 200,
                         "存的应该是转换后的值")

    def test_cloud_rejection_does_not_update_state(self):
        client = CountingClient(accept=False)
        provider = make_provider("fryer_maf04.json", "careli.fryer.maf04", client)
        result = provider.set_property("d1", 2, 4, 200)
        self.assertFalse(result["ok"], result)
        self.assertNotIn((2, 4), provider._states["d1"])


if __name__ == "__main__":
    unittest.main()
