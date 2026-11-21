"""Signing / encoding tests.

Offline we cannot replay a real gateway response, so the strongest available
checks are "server side simulations":

  * rebuild the signature from the submitted body and compare,
  * decrypt every submitted value and compare with the original,
  * decode responses produced by our own encoder with the right headers.

These pin the *invariants* the reverse engineering established: sorted key
order, one shared RC4 stream, ``/app`` trimming, fixed-width nonce minutes.
"""

import base64
import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.crypto import rc4, aes  # noqa: E402
from mihome_pc.xiaomi import sign  # noqa: E402

SSECURITY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
BASE = "https://api.mijia.tech/app"


def _nonce_for(minutes):
    """Deterministic nonce so tests are reproducible."""
    raw = b"\x00" * 8 + sign._int32_be(minutes)
    import base64 as b64
    return b64.b64encode(raw).decode("ascii")


class TestNonce(unittest.TestCase):
    def test_shape(self):
        nonce = sign.gen_nonce(now_ms=1760000000000, server_offset_ms=0)
        raw = base64.b64decode(nonce)
        self.assertEqual(len(raw), 12, "8 random bytes + 4-byte minutes")
        minutes = int.from_bytes(raw[8:], "big")
        self.assertEqual(minutes, 1760000000000 // 60000)

    def test_fixed_width_minutes(self):
        # A tiny timestamp must still produce a 4-byte minutes field.
        nonce = sign.gen_nonce(now_ms=60000, server_offset_ms=0)
        self.assertEqual(len(base64.b64decode(nonce)), 12)

    def test_offset_applied(self):
        nonce = sign.gen_nonce(now_ms=1760000000000, server_offset_ms=3600000)
        raw = base64.b64decode(nonce)
        self.assertEqual(int.from_bytes(raw[8:], "big"),
                         (1760000000000 + 3600000) // 60000)

    def test_unique(self):
        a = sign.gen_nonce()
        b = sign.gen_nonce()
        self.assertNotEqual(a, b)


class TestSessionSecurity(unittest.TestCase):
    def test_matches_java_formula(self):
        import hashlib
        nonce = _nonce_for(12345678)
        got = sign.session_security(SSECURITY, nonce)
        expect = base64.b64encode(hashlib.sha256(
            base64.b64decode(SSECURITY) + base64.b64decode(nonce)).digest()).decode()
        self.assertEqual(got, expect)

    def test_is_32_bytes(self):
        nonce = _nonce_for(12345678)
        self.assertEqual(len(base64.b64decode(sign.session_security(SSECURITY, nonce))), 32)


class TestSignature(unittest.TestCase):
    def setUp(self):
        self.ss = sign.session_security(SSECURITY, _nonce_for(29000000))

    def test_sorted_by_key(self):
        """Order of the input mapping must not matter: TreeMap sorts."""
        a = sign.signature("POST", "/miotspec/prop/get",
                           [("params", "P"), ("datasource", 1)], self.ss)
        b = sign.signature("POST", "/miotspec/prop/get",
                           [("datasource", 1), ("params", "P")], self.ss)
        self.assertEqual(a, b)

    def test_known_shape(self):
        import hashlib
        got = sign.signature("POST", "/miotspec/prop/get",
                             [("datasource", 1), ("params", "P")], self.ss)
        expect_text = "POST&/miotspec/prop/get&datasource=1&params=P&" + self.ss
        expect = base64.b64encode(
            hashlib.sha1(expect_text.encode()).digest()).decode()
        self.assertEqual(got, expect)

    def test_trims_app_prefix(self):
        a = sign.signature("POST", "/miotspec/prop/get", [], self.ss)
        b = sign.signature("POST", BASE + "/miotspec/prop/get", [], self.ss)
        self.assertEqual(a, b)

    def test_method_uppercased(self):
        a = sign.signature("post", "/x", [], self.ss)
        b = sign.signature("POST", "/x", [], self.ss)
        self.assertEqual(a, b)

    def test_requires_session_security(self):
        self.assertRaises(sign.SignError, sign.signature, "POST", "/x", [], "")


class TestEncodeRC4(unittest.TestCase):
    URL = BASE + "/miotspec/prop/get"
    PARAMS = [("params", '[{"did":"1","siid":2,"piid":1}]'), ("datasource", 1)]

    def _encode(self):
        nonce = _nonce_for(29000000)
        return sign.encode_rc4(self.URL, "POST", self.PARAMS, SSECURITY, nonce=nonce)

    def test_tail_fields(self):
        body, ctx = self._encode()
        keys = [k for k, _ in body]
        self.assertEqual(keys[-3:], ["signature", "_nonce", "ssecurity"])
        self.assertEqual(ctx["algorithm"], sign.ALGORITHM_RC4)

    def test_body_is_sorted(self):
        body, _ = self._encode()
        values = body[:-3]
        keys = [k for k, _ in values]
        self.assertEqual(keys, sorted(keys))

    def test_server_side_decrypt_roundtrip(self):
        """Simulate the gateway: verify signature, decrypt every value."""
        body, ctx = self._encode()
        ss = ctx["session_security"]
        payload = body[:-3]
        received = dict(body)

        # 1. signature covers the encrypted map (including rc4_hash__)
        self.assertEqual(body[-3][1], sign.signature("POST", self.URL, payload, ss))

        # 2. rc4_hash__ was signed over the *plain* params
        stream = rc4.new_stream(base64.b64decode(ss))
        plain = {}
        for key, value in payload:
            plain[key] = stream.crypt(base64.b64decode(value)).decode("utf-8")
        expected_hash = sign.signature(
            "POST", self.URL,
            [(k, v) for k, v in plain.items() if k != "rc4_hash__"], ss)
        self.assertEqual(plain["rc4_hash__"], expected_hash)

        # 3. values survive the round trip
        self.assertEqual(plain["params"], self.PARAMS[0][1])
        self.assertEqual(plain["datasource"], "1")
        self.assertEqual(received["ssecurity"], SSECURITY)

    def test_nonce_and_ssecurity_in_body(self):
        body, ctx = self._encode()
        mapping = dict(body)
        self.assertEqual(mapping["_nonce"], ctx["nonce"])
        self.assertEqual(mapping["ssecurity"], SSECURITY)


class TestEncodeAES(unittest.TestCase):
    URL = BASE + "/miotspec/prop/set"

    def test_server_side_decrypt_roundtrip(self):
        nonce = _nonce_for(29000000)
        iv = bytes(bytearray(range(12)))
        body, ctx = sign.encode_aes(
            self.URL, "POST", [("data", '{"x":1}')], SSECURITY,
            nonce=nonce, iv=iv)
        mapping = dict(body)
        self.assertEqual(ctx["algorithm"], sign.ALGORITHM_AES)
        self.assertNotIn("ssecurity", mapping, "AES mode omits ssecurity")
        self.assertEqual(mapping["aes_hash__"], base64.b64encode(iv).decode())

        ss = ctx["session_security"]
        key = base64.b64decode(ss)
        blob = base64.b64decode(mapping["data"])
        plain = aes.gcm_decrypt(key, iv, blob[:-16], blob[-16:])
        self.assertEqual(plain.decode(), '{"x":1}')

        signed = [(k, v) for k, v in body
                  if k not in ("signature", "_nonce")]
        self.assertEqual(mapping["signature"],
                         sign.signature("POST", self.URL, signed, ss))


class TestDecodeResponse(unittest.TestCase):
    def setUp(self):
        self.ss = sign.session_security(SSECURITY, _nonce_for(29000000))

    def _wrap_rc4(self, obj, compress=True):
        raw = json.dumps(obj).encode("utf-8")
        if compress:
            raw = gzip.compress(raw)
        return rc4.encrypt_b64(base64.b64decode(self.ss), raw)

    def test_plain_json_passthrough(self):
        payload = {"code": 0, "message": "ok"}
        self.assertEqual(sign.decode_response(self.ss, json.dumps(payload)), payload)

    def test_rc4_gzip(self):
        payload = {"code": 0, "result": {"devices": [1, 2, 3]}}
        self.assertEqual(sign.decode_response(self.ss, self._wrap_rc4(payload)), payload)

    def test_rc4_plain_without_gzip(self):
        payload = {"code": 0}
        blob = self._wrap_rc4(payload, compress=False)
        self.assertEqual(sign.decode_response(self.ss, blob), payload)

    def test_header_drives_gunzip(self):
        # GZIP declared in header but body is not gzipped -> must raise clearly
        blob = rc4.encrypt_b64(base64.b64decode(self.ss), b'{"a":1}')
        self.assertRaises(sign.SignError, sign.decode_response, self.ss, blob, "GZIP")

    def test_garbage_raises(self):
        self.assertRaises(sign.SignError, sign.decode_response, self.ss, "!!!not-base64!!!")

    def test_aes_response(self):
        key = base64.b64decode(self.ss)
        iv = bytes(bytearray(range(12)))
        raw = gzip.compress(json.dumps({"code": 0, "ok": True}).encode())
        ct, tag = aes.gcm_encrypt(key, iv, raw)
        blob = base64.b64encode(ct + tag).decode()
        got = sign.decode_response_aes(self.ss, blob, base64.b64encode(iv).decode())
        self.assertEqual(got, {"code": 0, "ok": True})


class TestApiPath(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(sign.api_path(BASE + "/miotspec/prop/get"), "/miotspec/prop/get")
        self.assertEqual(sign.api_path("/app/miotspec/prop/get"), "/miotspec/prop/get")
        self.assertEqual(sign.api_path("/miotspec/action"), "/miotspec/action")

    def test_keeps_query_out(self):
        self.assertEqual(
            sign.api_path("https://miot-spec.org/miot-spec-v2/instance?urn=x"),
            "/miot-spec-v2/instance")


if __name__ == "__main__":
    unittest.main()
