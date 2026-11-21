"""Login flow tests driven by a fake transport (no network, no account)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.xiaomi import auth  # noqa: E402


class FakeTransport:
    """Replays a scripted list of responses and records the requests.

    A scripted entry may be an exception instance, which is raised instead of
    being returned -- that is how a socket timeout during the long poll is
    simulated.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def _next(self, url):
        if not self.responses:
            raise AssertionError("unexpected request: %s" % url)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url, data=None, headers=None, timeout=None):
        self.requests.append(("GET", url, headers or {}))
        return self._next(url)

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append(("POST", url, headers or {}))
        return self._next(url)


def resp(payload, headers=None, status=200):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return auth.Response("https://account.xiaomi.com/x", body, headers or {}, status)


def prefixed(payload, headers=None):
    """Account endpoints always prepend the safe prefix."""
    return resp(auth.SAFE_PREFIX + json.dumps(payload), headers)


class TestHelpers(unittest.TestCase):
    def test_strip_safe_prefix(self):
        self.assertEqual(auth.strip_safe_prefix("&&&START&&&{\"a\":1}"), '{"a":1}')
        self.assertEqual(auth.strip_safe_prefix('{"a":1}'), '{"a":1}')
        self.assertEqual(auth.strip_safe_prefix(None), "")

    def test_json_from_body_with_prefix(self):
        self.assertEqual(auth._json_from_body("&&&START&&&{\"code\":0}"), {"code": 0})

    def test_bad_json_raises(self):
        self.assertRaises(auth.LoginError, auth._json_from_body, "not json")

    def test_device_id_shape(self):
        device_id = auth.new_device_id()
        self.assertEqual(len(device_id), 16)
        self.assertTrue(device_id.isalnum())
        self.assertEqual(device_id, device_id.lower())

    def test_cookie_value_from_set_cookie(self):
        response = resp("", {"Set-Cookie": "serviceToken=abc123; Path=/; HttpOnly"})
        self.assertEqual(auth._cookie_value(response, "serviceToken"), "abc123")

    def test_cookie_value_absent(self):
        self.assertIsNone(auth._cookie_value(resp("", {}), "serviceToken"))


class TestQrRequest(unittest.TestCase):
    def test_request_qr(self):
        transport = FakeTransport([prefixed({
            "code": 0,
            "qr": "https://qr.example/1.png",
            "lp": "https://account.xiaomi.com/lp?id=1",
            "loginUrl": "https://account.xiaomi.com/lu?id=1",
        })])
        login = auth.QrLogin(transport=transport, device_id="dev123")
        qr, lp, lu = login.request_qr()
        self.assertEqual(qr, "https://qr.example/1.png")
        self.assertTrue(lp.endswith("id=1"))
        self.assertTrue(lu.endswith("id=1"))
        method, url, headers = transport.requests[0]
        self.assertEqual(method, "GET")
        self.assertIn("/longPolling/loginUrl", url)
        self.assertIn("sid=xiaomiio", url)
        self.assertIn("_qrsize=240", url)
        self.assertIn("deviceId=dev123", headers["Cookie"])

    def test_request_qr_failure_code(self):
        transport = FakeTransport([prefixed({"code": 70001, "description": "boom"})])
        login = auth.QrLogin(transport=transport)
        self.assertRaises(auth.LoginError, login.request_qr)

    def test_request_qr_missing_lp(self):
        transport = FakeTransport([prefixed({"code": 0, "qr": "x"})])
        login = auth.QrLogin(transport=transport)
        self.assertRaises(auth.LoginError, login.request_qr)


class TestWaitForScan(unittest.TestCase):
    def _login(self, responses, **kwargs):
        transport = FakeTransport(responses)
        login = auth.QrLogin(transport=transport, device_id="dev123", **kwargs)
        login.qr_url = "https://qr"
        login.lp_url = "https://lp"
        return login, transport

    def test_polls_until_confirmed_then_exchanges_token(self):
        login, transport = self._login([
            prefixed({"code": 70016, "description": "waiting"}),
            prefixed({"code": 0, "location": "https://account.xiaomi.com/sts",
                      "ssecurity": "SSEC", "passToken": "PT",
                      "userId": "42", "cUserId": "cu"}),
            resp("", {"Set-Cookie": "serviceToken=TOKEN"}),
        ])
        credentials = login.wait_for_scan(timeout=10)
        self.assertEqual(credentials.service_token, "TOKEN")
        self.assertEqual(credentials.ssecurity, "SSEC")
        self.assertEqual(credentials.pass_token, "PT")
        self.assertEqual(credentials.user_id, "42")
        self.assertEqual(credentials.c_user_id, "cu")
        self.assertEqual(credentials.device_id, "dev123")
        self.assertEqual(len(transport.requests), 3)

    def test_retries_socket_timeout_during_long_poll(self):
        """A blocking long poll outliving the socket timeout must be retried."""
        login, transport = self._login([
            auth.TransientNetworkError("timed out"),
            auth.TransientNetworkError("timed out"),
            prefixed({"code": 0, "location": "https://account.xiaomi.com/sts",
                      "ssecurity": "SSEC", "passToken": "PT",
                      "userId": "42", "cUserId": "cu"}),
            resp("", {"Set-Cookie": "serviceToken=AFTER_RETRY"}),
        ])
        credentials = login.wait_for_scan(timeout=30)
        self.assertEqual(credentials.service_token, "AFTER_RETRY")
        self.assertEqual(len(transport.requests), 4, "two retries then success")

    def test_hard_failure_code_propagates(self):
        login, _ = self._login([prefixed({"code": 70001, "description": "denied"})])
        self.assertRaises(auth.LoginError, login.wait_for_scan, 5)

    def test_timeout(self):
        login, _ = self._login([prefixed({"code": 70016})] * 50)
        self.assertRaises(auth.LoginTimeout, login.wait_for_scan, 0)

    def test_missing_ssecurity_raises(self):
        login, _ = self._login([
            prefixed({"code": 0, "location": "https://x", "passToken": "PT"}),
        ])
        self.assertRaises(auth.LoginError, login.wait_for_scan, 5)

    def test_requires_request_qr_first(self):
        login = auth.QrLogin(transport=FakeTransport([]))
        self.assertRaises(auth.LoginError, login.wait_for_scan, 1)

    def test_ssecurity_from_extension_pragma(self):
        login, _ = self._login([
            resp(auth.SAFE_PREFIX + json.dumps(
                {"code": 0, "location": "https://x", "userId": "7"}),
                {"Extension-Pragma": json.dumps({"ssecurity": "FROM_PRAGMA"})}),
            resp("", {"Set-Cookie": "serviceToken=T2"}),
        ])
        credentials = login.wait_for_scan(timeout=5)
        self.assertEqual(credentials.ssecurity, "FROM_PRAGMA")


class TestRefresh(unittest.TestCase):
    def _creds(self):
        return auth.Credentials(user_id="42", c_user_id="cu", service_token="OLD",
                                ssecurity="SS", pass_token="PT", device_id="dev123")

    def test_refresh_success(self):
        transport = FakeTransport([
            prefixed({"code": 0, "location": "https://account.xiaomi.com/sts"}),
            resp("", {"Set-Cookie": "serviceToken=NEW"}),
        ])
        refreshed = auth.refresh_service_token(self._creds(), transport=transport)
        self.assertEqual(refreshed.service_token, "NEW")
        self.assertEqual(refreshed.ssecurity, "SS")
        self.assertEqual(refreshed.pass_token, "PT")
        # the original must not be mutated
        self.assertEqual(self._creds().service_token, "OLD")

    def test_refresh_carries_pass_token_cookie(self):
        transport = FakeTransport([
            prefixed({"code": 0, "location": "https://x"}),
            resp("", {"Set-Cookie": "serviceToken=NEW"}),
        ])
        auth.refresh_service_token(self._creds(), transport=transport)
        _, url, headers = transport.requests[0]
        self.assertIn("/pass/serviceLogin", url)
        self.assertIn("sid=xiaomiio", url)
        self.assertIn("passToken=PT", headers["Cookie"])
        self.assertIn("userId=42", headers["Cookie"])

    def test_refresh_expired_raises(self):
        transport = FakeTransport([prefixed({"code": 70016, "description": "expired"})])
        self.assertRaises(auth.NotAuthorized,
                          auth.refresh_service_token, self._creds(), transport)

    def test_refresh_without_location_raises(self):
        transport = FakeTransport([prefixed({"code": 0})])
        self.assertRaises(auth.NotAuthorized,
                          auth.refresh_service_token, self._creds(), transport)

    def test_refresh_without_token_raises(self):
        transport = FakeTransport([
            prefixed({"code": 0, "location": "https://x"}),
            resp("", {}),
        ])
        self.assertRaises(auth.NotAuthorized,
                          auth.refresh_service_token, self._creds(), transport)


class TestCredentialStorage(unittest.TestCase):
    def test_roundtrip(self):
        credentials = auth.Credentials(user_id="1", c_user_id="c", service_token="t",
                                       ssecurity="s", pass_token="p", device_id="d")
        # TemporaryDirectory cleans up after itself: the embedded Python falls
        # back to the CWD for %TEMP% under the sandbox, so a bare mkdtemp()
        # would litter the project root.
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "creds.json")
            auth.save_credentials(credentials, path)
            loaded = auth.load_credentials(path)
            self.assertEqual(loaded.to_dict(), credentials.to_dict())

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(auth.load_credentials(os.path.join(directory, "no.json")))

    def test_login_cache_path(self):
        self.assertTrue(auth.login_cache_path("/tmp/x").endswith("credentials.json"))


if __name__ == "__main__":
    unittest.main()
