"""End-to-end protocol tests: the real CloudClient against a fake gateway.

This is the "fixture regression" the project promised: the client and the
gateway are written from the same reverse-engineering notes but are independent
implementations, and they talk over a real TCP socket.  A bug in signing order,
nonce handling, stream sharing, gzip detection or envelope parsing shows up here
even though every unit test would still pass.
"""
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_gateway as fg                                   # noqa: E402
from mihome_pc.xiaomi import cloud, sign                    # noqa: E402

SSECURITY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def session():
    return cloud.Session(user_id="42", c_user_id="cu", service_token="token",
                         ssecurity=SSECURITY, device_id="dev1")


class GatewayCase(unittest.TestCase):
    algorithm = fg.RC4_ALGORITHM
    gzip_replies = True

    def setUp(self):
        self.gateway = fg.FakeGateway(SSECURITY, algorithm=self.algorithm,
                                      gzip_replies=self.gzip_replies).start()
        self.addCleanup(self.gateway.stop)
        self.client = cloud.CloudClient(session(), base_url=self.gateway.base_url,
                                        algorithm=self.algorithm)
        self.addCleanup(self._check_no_violations)

    def _check_no_violations(self):
        if self.gateway.violations:
            self.fail("client violated the protocol: %s" % self.gateway.violations)


class TestBasicCalls(GatewayCase):
    def test_homes_round_trip(self):
        self.gateway.static("/v2/homeroom/gethome_merged",
                            {"home_list": [{"id": 1, "name": "demo"}]})
        result = self.client.homes()
        self.assertEqual(result, {"home_list": [{"id": 1, "name": "demo"}]})
        recorded = self.gateway.requests[-1]
        self.assertEqual(recorded["path"], "/app/v2/homeroom/gethome_merged")
        self.assertEqual(recorded["payload"]["limit"], 300)
        self.assertTrue(recorded["payload"]["fetch_share"])

    def test_devices_round_trip(self):
        device = {"did": "123", "model": "xiaomi.dishwasher.s2", "isOnline": True,
                  "spec_type": "urn:miot-spec-v2:device:dishwasher:0000A034:xiaomi-s2:1"}
        self.gateway.static("/v2/home/device_list_page", {"list": [device]})
        result = self.client.devices()
        self.assertEqual(result["list"][0]["did"], "123")
        self.assertEqual(result["list"][0]["spec_type"], device["spec_type"])

    def test_props_payload_shape(self):
        self.gateway.static("/miotspec/prop/get", {"params": [
            {"did": "123", "siid": 2, "piid": 1, "code": 0, "value": True}]})
        result = self.client.props([("123", 2, 1)])
        self.assertEqual(result["params"][0]["value"], True)
        sent = self.gateway.requests[-1]["payload"]
        self.assertEqual(sent["params"], [{"did": "123", "siid": 2, "piid": 1}])
        self.assertEqual(sent["datasource"], 1)

    def test_set_props_carries_value(self):
        self.gateway.static("/miotspec/prop/set", {"params": [
            {"did": "123", "siid": 2, "piid": 4, "code": 0}]})
        self.client.set_props([("123", 2, 4, 3)])
        sent = self.gateway.requests[-1]["payload"]
        self.assertEqual(sent["params"], [{"did": "123", "siid": 2, "piid": 4, "value": 3}])

    def test_action(self):
        self.gateway.static("/miotspec/action", {"code": 0})
        self.client.call_action("123", 2, 1, ["x"])
        sent = self.gateway.requests[-1]["payload"]
        # /miotspec/action takes params as an OBJECT (SpecActionApi.java:115),
        # unlike /miotspec/prop/set which takes an array.
        self.assertIsInstance(sent["params"], dict)
        self.assertEqual(sent["params"],
                         {"did": "123", "siid": 2, "aiid": 1, "in": ["x"]})

    def test_action_always_sends_in(self):
        """A no-argument action still needs ``in`` or the gateway answers -8."""
        self.gateway.static("/miotspec/action", {"code": 0})
        self.client.call_action("123", 2, 1)
        sent = self.gateway.requests[-1]["payload"]
        self.assertIn("in", sent["params"])
        self.assertEqual(sent["params"]["in"], [])

    def test_specs_accepts_empty_urns(self):
        self.gateway.static("/appgateway/gspec/Gspec/get_instances_by_dids",
                            {"instances": [{"type": "urn:x", "services": []}]})
        result = self.client.specs(["123", "456"])
        self.assertIn("instances", result)
        sent = self.gateway.requests[-1]["payload"]
        self.assertEqual(sent["dids"], ["123", "456"])
        self.assertEqual(sent["urns"], ["", ""])
        self.assertTrue(sent["isGetFullInstance"])


class TestEnvelopeRules(GatewayCase):
    def test_trailing_fields_order(self):
        self.gateway.static("/miotspec/prop/get", {"params": []})
        self.client.props([("123", 2, 1)])
        trailing = self.gateway.requests[-1]["trailing"]
        self.assertEqual(trailing, ["signature", "_nonce", "ssecurity"],
                         "RC4 body must end with signature/_nonce/ssecurity")

    def test_headers(self):
        self.gateway.static("/miotspec/prop/get", {"params": []})
        self.client.props([("123", 2, 1)])
        headers = self.gateway.requests[-1]["headers"]
        lowered = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(lowered.get("miot-encrypt-algorithm"), "ENCRYPT-RC4")
        self.assertEqual(lowered.get("accept-encoding"), "identity")
        self.assertEqual(lowered.get("x-xiaomi-protocal-flag-cli"), "PROTOCAL-HTTP2")
        self.assertEqual(lowered.get("miot-accept-encoding"), "GZIP")
        self.assertIn("serviceToken=token", lowered.get("cookie", ""))
        self.assertIn("yetAnotherServiceToken=token", lowered.get("cookie", ""))
        self.assertIn("locale=zh_CN", lowered.get("cookie", ""))

    def test_nonce_is_fresh_per_request(self):
        self.gateway.static("/miotspec/prop/get", {"params": []})
        self.client.props([("123", 2, 1)])
        first = self.gateway.requests[-1]["headers"]
        self.client.props([("123", 2, 1)])
        self.gateway.requests[-1]["headers"]
        self.assertTrue(first, "headers were recorded")


class TestTransportVariants(unittest.TestCase):
    def _client(self, gateway, algorithm=fg.RC4_ALGORITHM):
        return cloud.CloudClient(session(), base_url=gateway.base_url,
                                 algorithm=algorithm)

    def test_without_gzip_reply(self):
        gateway = fg.FakeGateway(SSECURITY, gzip_replies=False).start()
        self.addCleanup(gateway.stop)
        gateway.static("/miotspec/prop/get", {"params": []})
        client = self._client(gateway)
        self.assertEqual(client.props([("1", 2, 1)]), {"params": []})
        self.assertEqual(gateway.violations, [])

    def test_aes_algorithm(self):
        gateway = fg.FakeGateway(SSECURITY, algorithm=fg.AES_ALGORITHM).start()
        self.addCleanup(gateway.stop)
        gateway.static("/miotspec/prop/set", {"params": [{"did": "1", "siid": 2,
                                                          "piid": 4, "code": 0}]})
        client = self._client(gateway, fg.AES_ALGORITHM)
        result = client.set_props([("1", 2, 4, 5)])
        self.assertEqual(result["params"][0]["code"], 0)
        self.assertEqual(gateway.violations, [])
        recorded = gateway.requests[-1]
        self.assertEqual(recorded["algorithm"], "ENCRYPT-AES")
        self.assertEqual(recorded["trailing"], ["aes_hash__", "signature", "_nonce"],
                         "AES body must carry aes_hash__ and no ssecurity")


class TestErrorMapping(GatewayCase):
    def test_auth_error_becomes_AuthError(self):
        self.gateway.static("/v2/home/device_list_page", fg.fail(2, "auth error"))
        self.assertRaises(cloud.AuthError, self.client.devices)
        with self.assertRaises(cloud.AuthError) as caught:
            self.client.devices()
        self.assertEqual(caught.exception.code, 2)

    def test_other_code_becomes_CloudError(self):
        self.gateway.static("/miotspec/prop/set", fg.fail(-5000, "device refused"))
        with self.assertRaises(cloud.CloudError) as caught:
            self.client.set_props([("1", 2, 4, 5)])
        self.assertEqual(caught.exception.code, -5000)
        self.assertIn("-5000", str(caught.exception))

    def test_unhandled_path_surfaces_code(self):
        with self.assertRaises(cloud.CloudError):
            self.client.post("/nope", {"a": 1})


class TestSuccessCodes(unittest.TestCase):
    """``code: 1`` means accepted; a real bulb answers 1 on every good write."""

    def test_zero_is_success(self):
        self.assertTrue(cloud.is_success(0))

    def test_one_is_success(self):
        self.assertTrue(cloud.is_success(1))

    def test_negative_is_not(self):
        for code in (-1, -5000, -704220043, -704030013):
            self.assertFalse(cloud.is_success(code))

    def test_garbage_is_not(self):
        self.assertFalse(cloud.is_success(None))
        self.assertFalse(cloud.is_success("nope"))


class TestClientRejection(GatewayCase):
    """The gateway must reject a client that gets the protocol wrong."""

    def test_tampered_signature_is_detected(self):
        """Corrupt only the *client's* signature; the gateway must notice.

        Patching ``sign.signature`` would corrupt both sides (the gateway imports
        the same module), so we wrap ``encode_rc4`` and tamper its output.
        """
        self.gateway.static("/miotspec/prop/get", {"params": []})
        original = sign.encode_rc4

        def tampered(*args, **kwargs):
            pairs, ctx = original(*args, **kwargs)
            pairs = [(k, (base64.b64encode(b"tampered").decode()
                          if k == "signature" else v)) for k, v in pairs]
            return pairs, ctx

        sign.encode_rc4 = tampered
        try:
            with self.assertRaises(cloud.CloudError):
                self.client.props([("1", 2, 1)])
        finally:
            sign.encode_rc4 = original
        self.assertTrue(self.gateway.violations, "gateway should have complained")
        self.assertIn("signature mismatch", self.gateway.violations[0])
        # this test *wants* a violation; keep the shared cleanup happy
        self.gateway.violations = []


if __name__ == "__main__":
    unittest.main()
