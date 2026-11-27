"""Regression tests for QR-login cookie and redirect behavior (offline)."""
import json
import os
import sys
import unittest
from http.cookiejar import Cookie, CookieJar
from urllib.request import HTTPCookieProcessor, Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.xiaomi import auth  # noqa: E402


def seed_cookie(jar, name, value, domain=".xiaomi.com"):
    jar.set_cookie(Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path="/", path_specified=True, secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    ))


def resp(payload, headers=None):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return auth.Response("https://account.xiaomi.com/x", body, headers or {})


def prefixed(payload):
    return resp(auth.SAFE_PREFIX + json.dumps(payload))


class CookieTransport:
    """Scripted responses, but real CookieJar request-header behavior."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.cookies = CookieJar()
        self.requests = []
        self.processor = HTTPCookieProcessor(self.cookies)

    def get(self, url, data=None, headers=None, timeout=None):
        request = Request(url, headers=headers or {})
        self.processor.http_request(request)
        actual_headers = dict(headers or {})
        cookie = request.get_header("Cookie")
        if cookie:
            actual_headers["Cookie"] = cookie
        self.requests.append(("GET", url, actual_headers))
        if not self.responses:
            raise AssertionError("unexpected request: " + url)
        return self.responses.pop(0)


class RawResponse:
    def __init__(self, response):
        self.response = response
        self.headers = response.headers

    def geturl(self):
        return self.response.url

    def read(self):
        return self.response.text.encode("utf-8")


class RecordingOpener:
    def __init__(self, transport, response):
        self.processor = HTTPCookieProcessor(transport.cookies)
        self.response = response
        self.cookie = ""

    def open(self, request, timeout=None):
        self.processor.http_request(request)
        self.cookie = request.get_header("Cookie") or ""
        return RawResponse(self.response)


class TestTransportCookieMerging(unittest.TestCase):
    def test_existing_cookie_header_does_not_block_session_cookies(self):
        transport = auth.Transport()
        response = prefixed({
            "code": 0, "qr": "https://qr.example/1.png",
            "lp": "https://account.xiaomi.com/lp", "loginUrl": "https://account.xiaomi.com/lu",
        })
        opener = RecordingOpener(transport, response)
        transport._opener = opener
        seed_cookie(transport.cookies, "passToken", "PT_CANARY")
        seed_cookie(transport.cookies, "userId", "42")
        login = auth.QrLogin(transport=transport, device_id="dev123")
        login.request_qr()
        self.assertIn("deviceId=dev123", opener.cookie)
        self.assertIn("uLocale=zh_CN", opener.cookie)
        self.assertIn("passToken=PT_CANARY", opener.cookie)
        self.assertIn("userId=42", opener.cookie)


class TestExchangeCookieFallback(unittest.TestCase):
    def test_redirect_intermediate_cookie_is_read_from_jar(self):
        response = auth.Response("https://sts.api.io.mi.com/final", "", {})
        transport = CookieTransport([response])
        seed_cookie(transport.cookies, "serviceToken", "FROM_REDIRECT", ".io.mi.com")
        login = auth.QrLogin(transport=transport, device_id="dev123")
        token = login.exchange_service_token("https://sts.api.io.mi.com/sts")
        self.assertEqual(token, "FROM_REDIRECT")

    def test_refresh_also_reads_redirect_cookie_from_jar(self):
        response = auth.Response("https://sts.api.io.mi.com/final", "", {})
        transport = CookieTransport([
            prefixed({"code": 0, "location": "https://sts.api.io.mi.com/sts"}),
            response,
        ])
        seed_cookie(transport.cookies, "serviceToken", "REFRESH_REDIRECT", ".io.mi.com")
        credentials = auth.Credentials(
            user_id="42", service_token="OLD", ssecurity="SS",
            pass_token="PT_CANARY", device_id="dev123")
        refreshed = auth.refresh_service_token(credentials, transport=transport)
        self.assertEqual(refreshed.service_token, "REFRESH_REDIRECT")
        self.assertEqual(len(transport.requests), 2)


class TestStaleLocationFallback(unittest.TestCase):
    def test_failed_old_location_renews_via_service_login_once(self):
        transport = CookieTransport([
            prefixed({
                "code": 0, "location": "https://account.xiaomi.com/old-sts",
                "ssecurity": "OLD_SSEC", "cUserId": "cu",
            }),
            resp("", {}),
            prefixed({
                "code": 0, "location": "https://account.xiaomi.com/new-sts",
                "ssecurity": "NEW_SSEC",
            }),
            resp("", {"Set-Cookie": "serviceToken=NEW_TOKEN"}),
        ])
        seed_cookie(transport.cookies, "passToken", "PT_CANARY")
        seed_cookie(transport.cookies, "userId", "42")
        login = auth.QrLogin(transport=transport, device_id="dev123")
        login.lp_url = "https://account.xiaomi.com/lp"
        credentials = login.wait_for_scan(timeout=5)
        self.assertEqual(credentials.service_token, "NEW_TOKEN")
        self.assertEqual(credentials.ssecurity, "NEW_SSEC")
        self.assertEqual(credentials.pass_token, "PT_CANARY")
        self.assertEqual(credentials.user_id, "42")
        self.assertEqual(len(transport.requests), 4)
        method, url, headers = transport.requests[2]
        self.assertIn("/pass/serviceLogin", url)
        self.assertIn("sid=xiaomiio", url)
        self.assertIn("passToken=PT_CANARY", headers["Cookie"])
        self.assertIn("userId=42", headers["Cookie"])


if __name__ == "__main__":
    unittest.main()
