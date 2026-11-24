"""Xiaomi account login: QR scan, passToken refresh, serviceToken exchange.

Facts pinned from the APK (``com/xiaomi/accountsdk/account/XMPassport.java``):

* Account endpoints prefix their JSON body with ``&&&START&&&``
  (``PASSPORT_SAFE_PREFIX``, line 149); the real body starts after it.
* QR start: ``GET https://account.xiaomi.com/longPolling/loginUrl?sid=<sid>``
  returns ``{code, lp, loginUrl, qr}`` (line 713-731).
* The poll result carries ``location``, ``ssecurity``, ``passToken``,
  ``cUserId`` (parseLoginResult, line 1117-1161).
* Refreshing without a scan: ``GET /pass/serviceLogin?_json=true&sid=<sid>``
  with the ``passToken`` cookie answers ``code == 0`` plus a ``location`` that
  hands out a fresh ``serviceToken``.

``sid`` matters: ``api.mijia.tech`` / ``api.io.mi.com`` only accept a
``serviceToken`` minted for ``sid=xiaomiio``.  A ``sid=mijia`` token goes to a
third-party STS and is rejected with ``{"code":2,"message":"auth error"}``.
"""
import contextlib
import json
import os
import random
import socket
import string
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

SAFE_PREFIX = "&&&START&&&"
ACCOUNT_HOST = "https://account.xiaomi.com"
SID_MIHOME = "xiaomiio"

DEFAULT_TIMEOUT = 20
# The QR code image itself is served by Xiaomi, so no local QR encoder is needed.
QR_SIZE = 240


class LoginError(Exception):
    """Login flow failed."""


class TransientNetworkError(LoginError):
    """A retryable transport hiccup (socket timeout, reset, ...)."""


class LoginTimeout(LoginError):
    """The user never confirmed the QR code."""


class NotAuthorized(LoginError):
    """passToken is stale and the user has to scan again."""


class Credentials:
    """What we need to keep after a successful login."""

    __slots__ = ("user_id", "c_user_id", "service_token", "ssecurity",
                 "pass_token", "device_id")

    def __init__(self, user_id="", c_user_id="", service_token="", ssecurity="",
                 pass_token="", device_id=""):
        self.user_id = user_id
        self.c_user_id = c_user_id
        self.service_token = service_token
        self.ssecurity = ssecurity
        self.pass_token = pass_token
        self.device_id = device_id

    def to_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in (data or {}).items()
                          if k in cls.__slots__})

    def __repr__(self):
        # 不要在 repr 里放任何 token 片段：repr 会进日志/异常信息
        return "<Credentials user=%s authenticated=%s>" % (
            self.user_id, bool(self.service_token))


def new_device_id():
    """Android-ish device id: lowercase alphanumeric, 16 chars."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(16))


def strip_safe_prefix(text):
    """Account endpoints prepend ``&&&START&&&`` to their JSON bodies."""
    if text is None:
        return ""
    text = text.strip()
    if text.startswith(SAFE_PREFIX):
        return text[len(SAFE_PREFIX):]
    return text


def _json_from_body(text):
    body = strip_safe_prefix(text)
    try:
        return json.loads(body)
    except ValueError as exc:
        raise LoginError("account response is not JSON: %s" % exc)


class Transport:
    """Minimal cookie-aware HTTP GET/POST.

    Injected into :class:`QrLogin` and :func:`refresh_service_token` so the
    whole flow can be exercised offline with a fake transport.
    """

    def __init__(self, timeout=DEFAULT_TIMEOUT, user_agent=None):
        self.timeout = timeout
        self.user_agent = user_agent or (
            "Android-7.1.1-1.0.0-ONEPLUS A3010-136-APP-xiaomi-smart-home")
        self.cookies = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self.cookies))

    def _request(self, url, data=None, headers=None, timeout=None):
        merged = {"User-Agent": self.user_agent}
        if headers:
            merged.update(headers)
        if data is not None:
            merged.setdefault("Content-Type", "application/x-www-form-urlencoded")
            data = urlencode(data).encode("utf-8")
        request = Request(url, data=data, headers=merged)
        try:
            response = self._opener.open(request, timeout=timeout or self.timeout)
            return Response(response.geturl(), response.read().decode("utf-8", "replace"),
                            dict(response.headers.items()))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            return Response(url, body, dict(exc.headers.items()) if exc.headers else {},
                            status=exc.code)
        except URLError as exc:
            raise LoginError("network error: %s" % _safe_reason(exc.reason))
        except socket.timeout:
            # A blocking long-poll legitimately outlives the socket timeout;
            # the caller decides whether to retry.
            raise TransientNetworkError("timed out waiting for the server")

    def get(self, url, data=None, headers=None, timeout=None):
        return self._request(url, data=data, headers=headers, timeout=timeout)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._request(url, data=data or {}, headers=headers, timeout=timeout)


class Response:
    __slots__ = ("url", "text", "headers", "status")

    def __init__(self, url, text, headers, status=200):
        self.url = url
        self.text = text
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.status = status

    def json(self):
        return _json_from_body(self.text)

    def header(self, name):
        return self.headers.get(name.lower())

    def __repr__(self):
        return "<Response %s %s %dB>" % (self.status, self.url, len(self.text or ""))


class QrLogin:
    """Two-step QR login: fetch the code, then block until the phone confirms."""

    def __init__(self, transport=None, sid=SID_MIHOME, device_id=None,
                 locale="zh_CN", qr_size=QR_SIZE):
        self.transport = transport or Transport()
        self.sid = sid
        self.device_id = device_id or new_device_id()
        self.locale = locale
        self.qr_size = qr_size
        # populated by request_qr()
        self.qr_url = None
        self.lp_url = None
        self.login_url = None

    # -- step 1 --------------------------------------------------------
    def request_qr(self):
        """Ask for a fresh QR code. Returns the three URLs."""
        url = ACCOUNT_HOST + "/longPolling/loginUrl?" + urlencode({
            "sid": self.sid,
            "_qrsize": self.qr_size,
            "_dc": int(time.time() * 1000),
        })
        response = self.transport.get(url, headers={
            "Cookie": "deviceId=%s; uLocale=%s" % (self.device_id, self.locale),
        })
        payload = response.json()
        code = payload.get("code")
        if code != 0:
            raise LoginError("QR request failed: code=%s" % (code,))
        self.qr_url = payload.get("qr")
        self.lp_url = payload.get("lp")
        self.login_url = payload.get("loginUrl")
        if not (self.qr_url and self.lp_url):
            raise LoginError("QR response missing qr/lp")
        return self.qr_url, self.lp_url, self.login_url

    # -- step 2 --------------------------------------------------------
    def wait_for_scan(self, timeout=120, poll_timeout=55, poll_interval=2):
        """Block on the long-poll URL until the phone confirms.

        The gateway keeps the request open for tens of seconds, so a socket
        timeout is normal and must be retried, not treated as a failure.  We
        only give up on an explicit non-retryable result code or on the overall
        deadline.
        """
        if not self.lp_url:
            raise LoginError("request_qr() must be called first")
        deadline = time.time() + timeout
        cookie = "deviceId=%s; uLocale=%s" % (self.device_id, self.locale)
        last_error = None
        while time.time() < deadline:
            remaining = max(1, int(min(poll_timeout, deadline - time.time())))
            try:
                response = self.transport.get(
                    self.lp_url, headers={"Cookie": cookie}, timeout=remaining)
            except TransientNetworkError as exc:
                last_error = exc                     # normal for a long poll
                continue
            payload = response.json()
            code = payload.get("code")
            if code == 0:
                return self._credentials_from_login(payload, response)
            # 70016 and a few siblings mean "not confirmed yet"; keep waiting.
            if code in (70016, 70017, 70018, 70019, 70020, 70021):
                time.sleep(min(poll_interval, max(0, deadline - time.time())))
                continue
            if code is None:
                time.sleep(poll_interval)
                continue
            raise LoginError("QR poll failed: code=%s" % (code,))
        if last_error is not None:
            raise LoginTimeout("QR code was not confirmed within %ss" % timeout)
        raise LoginTimeout("QR code was not confirmed within %ss" % timeout)

    def _credentials_from_login(self, payload, response):
        location = payload.get("location") or response.header("location")
        ssecurity = payload.get("ssecurity")
        if not ssecurity:
            # older servers put it in the Extension-Pragma header
            pragma = response.header("Extension-Pragma") or response.header("extension-pragma")
            if pragma:
                with contextlib.suppress(ValueError):
                    ssecurity = json.loads(pragma).get("ssecurity")
        if not location:
            raise LoginError("login response has no location")
        if not ssecurity:
            raise LoginError("login response has no ssecurity")

        service_token = self.exchange_service_token(location)
        return Credentials(
            user_id=str(payload.get("userId") or ""),
            c_user_id=str(payload.get("cUserId") or response.header("cUserId") or ""),
            service_token=service_token,
            ssecurity=ssecurity,
            pass_token=payload.get("passToken") or response.header("passToken") or "",
            device_id=self.device_id,
        )

    # -- step 3: trade the login ticket for a serviceToken --------------
    def exchange_service_token(self, location):
        """``GET <location>`` answers ``Set-Cookie: serviceToken=...``."""
        url = location if "://" in location else ACCOUNT_HOST + location
        response = self.transport.get(url, headers={
            "Cookie": "deviceId=%s; uLocale=%s" % (self.device_id, self.locale),
        })
        token = _cookie_value(response, "serviceToken")
        if token:
            return token
        # Some responses embed the token in the body instead.
        try:
            payload = response.json()
        except LoginError:
            payload = {}
        token = payload.get("serviceToken")
        if token:
            return token
        raise LoginError("login response carried no serviceToken")


def _safe_reason(reason):
    """把网络错误的 reason 收敛成一个短标签。

    `reason` 原本可能带上完整 URL（含 ticket），所以不能直接拼进异常文本。
    """
    text = "%s" % (reason,)
    lowered = text.lower()
    if "refused" in lowered:
        return "connection refused"
    if "timed out" in lowered:
        return "timeout"
    if "name or service" in lowered or "getaddrinfo" in lowered:
        return "dns failure"
    if "reset" in lowered:
        return "connection reset"
    return "network unreachable"


def _cookie_value(response, name):
    raw = response.header("set-cookie")
    if not raw:
        return None
    # a single merged header may carry several cookies separated by commas
    for chunk in raw.replace("; ", ";").split(","):
        for part in chunk.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
    return None


def refresh_service_token(credentials, transport=None, sid=SID_MIHOME,
                          locale="zh_CN"):
    """Renew ``serviceToken`` from a stored ``passToken`` without scanning.

    Returns a new :class:`Credentials`, or raises :class:`NotAuthorized` when
    the passToken is no longer good.
    """
    transport = transport or Transport()
    cookie = "deviceId=%s; pass_o=; uLocale=%s" % (credentials.device_id, locale)
    if credentials.user_id:
        cookie += "; userId=" + credentials.user_id
    if credentials.c_user_id:
        cookie += "; cUserId=" + credentials.c_user_id
    if credentials.pass_token:
        cookie += "; passToken=" + credentials.pass_token

    url = ACCOUNT_HOST + "/pass/serviceLogin?" + urlencode({
        "_json": "true", "sid": sid, "_locale": locale,
    })
    response = transport.get(url, headers={"Cookie": cookie})
    payload = response.json()
    if payload.get("code") != 0:
        raise NotAuthorized("passToken rejected (code=%s); scan a new QR code"
                            % payload.get("code"))
    location = payload.get("location")
    if not location:
        raise NotAuthorized("no location in serviceLogin response")
    target = location if "://" in location else ACCOUNT_HOST + location
    final = transport.get(target, headers={"Cookie": cookie})
    token = _cookie_value(final, "serviceToken")
    if not token:
        raise NotAuthorized("no serviceToken issued for the stored passToken")

    refreshed = Credentials.from_dict(credentials.to_dict())
    refreshed.service_token = token
    if payload.get("ssecurity"):
        refreshed.ssecurity = payload["ssecurity"]
    return refreshed


def login_cache_path(base_dir=None):
    """Where the credentials live.

    ``%LOCALAPPDATA%`` is redirected for Store-packaged Python, so the caller
    decides; the default keeps everything under the user profile.
    """
    if base_dir:
        return os.path.join(base_dir, "credentials.json")
    home = os.path.expanduser("~")
    return os.path.join(home, ".mihome-pc", "credentials.json")


def save_credentials(credentials, path):
    """Write the credentials atomically.

    ``os.replace`` instead of remove-then-rename: the running server watches
    this file, and a reader landing in the gap between "remove" and "rename"
    would see "not logged in" and swap the UI back to demo data.  The pid
    suffix keeps two writers (web login + ``cli refresh``) from sharing a tmp
    file.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(credentials.to_dict(), handle, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)


def load_credentials(path):
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return Credentials.from_dict(json.load(handle))
