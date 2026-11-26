"""Xiaomi cloud RPC client (stdlib only).

Protocol facts are pinned in ``docs/reverse/01-网络与签名.md``; this module is
the thin transport on top of :mod:`mihome_pc.xiaomi.sign`.

Design notes
------------
* The payload is always sent as a **single form field** ``data`` holding the
  JSON string.  That is what Mi Home itself does
  (``DeviceCardApi$SpecPropertyApi.java:103``) and it side-steps any ambiguity
  about how the gateway walks multiple encrypted fields.
* Base URL for the CN region is ``https://api.mijia.tech/app``;
  ``https://api.io.mi.com/app`` is kept as a fallback because it still serves
  the same API.
* No third-party HTTP library: ``urllib.request`` plus a cookie header built by
  hand, because the gateway wants several cookies the stdlib cookie jar would
  otherwise normalise away.
"""

import base64
import json
import os
import contextlib
import ssl
import time

try:
    from urllib.request import Request, urlopen
    from urllib.parse import urlencode
    from urllib.error import HTTPError, URLError
except ImportError:                       # pragma: no cover
    from urllib2 import Request, urlopen, HTTPError, URLError
    from urllib import urlencode

from . import sign
from .. import logs

_log = logs.get("cloud")

# Verified against a real account on 2026-11-19:
#   https://api.io.mi.com/app    -> accepted  (sid=xiaomiio token works)
#   https://api.mijia.tech/app   -> auth error (code 2)
# The APK builds the mijia.tech host for CN, but that host does not accept this
# token, so `api.io.mi.com` stays the default and the other is kept only as a
# recorded finding.
BASE_IO = "https://api.io.mi.com/app"
BASE_CN = BASE_IO
BASE_MIJIA_TECH = "https://api.mijia.tech/app"
BASE_SPEC = "https://miot-spec.org"

# Region prefixes used for non-CN accounts (see ServerBean.OooOo in the APK).
REGION_HOSTS = {
    "cn": BASE_CN,
    "sg": "https://api.io.mi.com/app",
    "de": "https://de.api.io.mi.com/app",
    "us": "https://us.api.io.mi.com/app",
    "ru": "https://ru.api.io.mi.com/app",
    "i2": "https://in.api.io.mi.com/app",
}

DEFAULT_TIMEOUT = 20
USER_AGENT = "Android-7.1.1-1.0.0-ONEPLUS A3010-136-APP-xiaomi-smart-home"


class CloudError(Exception):
    """Transport or protocol level failure."""

    def __init__(self, message, code=None, payload=None):
        Exception.__init__(self, message)
        self.code = code
        self.payload = payload


class AuthError(CloudError):
    """The service token was rejected (usually: log in again)."""


class Session:
    """Everything needed to sign a request, plus the cookies the gateway wants."""

    FIELDS = ("user_id", "c_user_id", "service_token", "ssecurity",
              "device_id", "locale", "country_code", "timezone",
              "timezone_id", "channel", "region")

    def __init__(self, user_id="", c_user_id="", service_token="", ssecurity="",
                 device_id="", locale="zh_CN", country_code="CN",
                 timezone="GMT+08:00", timezone_id="Asia/Shanghai",
                 channel="MI_APP_STORE", region="cn"):
        self.user_id = user_id
        self.c_user_id = c_user_id
        self.service_token = service_token
        self.ssecurity = ssecurity
        self.device_id = device_id
        self.locale = locale
        self.country_code = country_code
        self.timezone = timezone
        self.timezone_id = timezone_id
        self.channel = channel
        self.region = region
        # Clock skew in ms between us and the gateway; refreshed opportunistically.
        self.server_offset_ms = 0

    # -- persistence ---------------------------------------------------
    def to_dict(self):
        return {name: getattr(self, name) for name in self.FIELDS}

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.FIELDS})

    def save(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(self.to_dict(), handle, indent=1)
        # Windows has ACLs rather than mode bits; failing to chmod is fine.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
        return path

    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return None
        with open(path) as handle:
            return cls.from_dict(json.load(handle))

    def cookie_header(self):
        parts = []
        if self.c_user_id:
            parts.append("cUserId=" + self.c_user_id)
        if self.service_token:
            parts.append("serviceToken=" + self.service_token)
            parts.append("yetAnotherServiceToken=" + self.service_token)
        parts.append("locale=" + self.locale)
        parts.append("countryCode=" + self.country_code)
        parts.append("channel=" + self.channel)
        if self.timezone_id:
            parts.append("timezone_id=" + self.timezone_id)
        if self.timezone:
            parts.append("timezone=" + self.timezone.replace("+", "%2B"))
        parts.append("is_daylight=0")
        parts.append("dst_offset=0")
        return "; ".join(parts)


class CloudClient:
    """Signed JSON RPC over the Mi Home gateway."""

    def __init__(self, session, base_url=None, timeout=DEFAULT_TIMEOUT,
                 algorithm=sign.ALGORITHM_RC4, verify_tls=True):
        self.session = session
        self.base_url = (base_url or REGION_HOSTS.get(session.region, BASE_CN)).rstrip("/")
        self.timeout = timeout
        self.algorithm = algorithm
        self._ssl = None
        if not verify_tls:
            self._ssl = ssl._create_unverified_context()   # noqa: SLF001 - explicit opt-in
        self.last_error = None

    # -- low level -----------------------------------------------------
    def _url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.base_url + path

    def _headers(self):
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Cookie": self.session.cookie_header(),
            "X-XIAOMI-PROTOCAL-FLAG-CLI": "PROTOCAL-HTTP2",
            "MIOT-ACCEPT-ENCODING": "GZIP",
            "MIOT-REQUEST-PAGE": "none",
        }

    def post(self, path, data, algorithm=None):
        """POST a ``data`` JSON payload, return the decoded JSON response."""
        algorithm = algorithm or self.algorithm
        url = self._url(path)
        payload = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
        params = [("data", payload)]

        if algorithm == sign.ALGORITHM_AES:
            pairs, ctx = sign.encode_aes(
                url, "POST", params, self.session.ssecurity,
                server_offset_ms=self.session.server_offset_ms)
        else:
            pairs, ctx = sign.encode_rc4(
                url, "POST", params, self.session.ssecurity,
                server_offset_ms=self.session.server_offset_ms)

        body = urlencode(pairs).encode("utf-8")
        headers = self._headers()
        headers["MIOT-ENCRYPT-ALGORITHM"] = ctx["algorithm"]
        if ctx["algorithm"] == sign.ALGORITHM_RC4:
            headers["Accept-Encoding"] = "identity"
        else:
            headers["X-XIAOMI-PROTOCAL-FLAG"] = "PROTOCAL-HTTPS"

        request = Request(url, data=body, headers=headers)
        started = time.time()
        try:
            response = urlopen(request, timeout=self.timeout,
                               context=self._ssl) if self._ssl else urlopen(
                request, timeout=self.timeout)
            raw = response.read()
            encoding = response.headers.get("MIOT-CONTENT-ENCODING")
            http_status = getattr(response, "status", getattr(response, "code", 200))
        except HTTPError as exc:
            raw = exc.read()
            encoding = exc.headers.get("MIOT-CONTENT-ENCODING") if exc.headers else None
            if not raw:
                _log.warning("http %s on %s after %dms", exc.code, path,
                             int((time.time() - started) * 1000))
                raise CloudError("HTTP %s on %s" % (exc.code, path))
            http_status = exc.code
        except URLError as exc:
            _log.warning("network error on %s after %dms: %s", path,
                         int((time.time() - started) * 1000), exc.reason)
            raise CloudError("network error on %s: %s" % (path, exc.reason))
        elapsed = time.time() - started

        text = raw.decode("utf-8", "replace")
        if ctx["algorithm"] == sign.ALGORITHM_AES:
            # The gateway answers with the same IV it saw (zd.OooOO0 reuses the
            # request's EncryptContext), so decrypt with our own ctx["iv"].
            iv_b64 = base64.b64encode(ctx["iv"]).decode("ascii")
            result = sign.decode_response_aes(
                ctx["session_security"], text, iv_b64, encoding)
        else:
            result = sign.decode_response(ctx["session_security"], text, encoding)

        if isinstance(result, dict) and "error" in result and "code" not in result:
            # e.g. a proxy or a protocol violation from the gateway itself
            _log.warning("call %s http=%s failed after %dms: %s", path, http_status,
                         int(elapsed * 1000), result.get("error"))
            raise CloudError("%s: %s" % (path, result.get("error")))
        if isinstance(result, dict) and result.get("code") not in (None, 0):
            code = result.get("code")
            message = result.get("message") or result.get("desc") or ""
            _log.warning("call %s http=%s code=%s after %dms: %s", path, http_status,
                         code, int(elapsed * 1000), message or "(no message)")
            if code in (2, 3, 401):
                raise AuthError("auth error: %s (code %s)" % (message, code),
                                code=code, payload=result)
            raise CloudError("%s: %s (code %s)" % (path, message, code),
                             code=code, payload=result)
        self.last_error = None
        self.last_elapsed = elapsed
        # 成功也要留一行：排障时"到底调没调、多久、成功码是多少"同样关键。
        _log.info("call %s http=%s code=%s in %dms (%d bytes)", path, http_status,
                  (result or {}).get("code") if isinstance(result, dict) else "-",
                  int(elapsed * 1000), len(text))
        return result.get("result", result) if isinstance(result, dict) else result

    # -- convenience wrappers -----------------------------------------
    def get(self, path, data=None):
        return self.post(path, data or {})

    def execute_text_directive(self, speaker_did, text, silent=True):
        """Ask a Xiaoai speaker to act as if the user had said ``text``.

        Real evidence (2026-11-20, xiaomi.wifispeaker.l05c):
        ``service 5 intelligent-speaker`` exposes

            action 5.4 execute-text-directive  in = [5.1 Text Content,
                                                    5.2 Silent Execution]

        and calling it through /miotspec/action returns ``code 0``.  This is the
        only way to wake a Mi TV once it is off: the TV's own ``turn-on`` lives
        in ``service 6 ble-control`` which the cloud cannot reach.
        """
        return self.call_action(speaker_did, 5, 4, [text, bool(silent)])

    def home_status(self, home_id, owner_id, modules=("fault", "working", "security")):
        """Home status popup data.

        Reverse-engineered from ``auto_page/model/OooO0OO.java:800-814``:
        ``POST /smh/get_act_data`` with ``home_id``, ``owner_id``,
        ``ignore_existing`` and a ``modules`` list.  The server evaluates the
        per-device rules and already returns ``count``, ``hidden`` and
        ``cur_value``; note ``target_prop`` arrives as ``"prop.2.2"``.
        """
        body = {
            "home_id": int(home_id),
            "ignore_existing": True,
            "owner_id": owner_id,
            "modules": list(modules),
        }
        return self.post("/smh/get_act_data", body)

    def homes(self):
        """Merged home list along with rooms and device ordering."""
        body = {"fg": True, "fetch_share": True, "fetch_share_dev": True,
                "fetch_cariot": True, "limit": 300, "app_ver": 7, "plat_form": 0}
        return self.post("/v2/homeroom/gethome_merged", body)

    def devices(self, home_id=None, start_did=None, limit=200):
        """All devices visible to the account (paged).

        Each record carries ``did``/``model``/``name``/``token``/``localip``
        and — importantly — ``spec_type``, which IS the MIoT spec URN.
        """
        body = {"get_split_device": True, "dids": []}
        if home_id:
            body["home_id"] = home_id
        if start_did:
            body["start_did"] = start_did
        body["limit"] = limit
        return self.post("/v2/home/device_list_page", body)

    def props(self, items, datasource=1):
        """Read properties: ``items`` = [(did, siid, piid), ...]."""
        body = {"params": [{"did": d, "siid": s, "piid": p} for d, s, p in items],
                "datasource": datasource}
        return self.post("/miotspec/prop/get", body)

    def set_props(self, items):
        """Write properties: ``items`` = [(did, siid, piid, value), ...]."""
        body = {"params": [{"did": d, "siid": s, "piid": p, "value": v}
                           for d, s, p, v in items]}
        return self.post("/miotspec/prop/set", body)

    def call_action(self, did, siid, aiid, args=None):
        # Verified against the live gateway: omitting `in` for a no-argument
        # action is rejected with `-8 data type not valid`; an empty array is
        # required.
        params = {"did": did, "siid": siid, "aiid": aiid, "in": list(args or [])}
        # /miotspec/action takes **params as an object**, unlike prop/set which
        # takes an array.  Evidence: DeviceCardApi.java:115
        #   jSONObject.put("params", jSONObject2);   // jSONObject2 is a JSONObject
        # Sending an array answers "-8 data type not valid".
        return self.post("/miotspec/action", {"params": params})

    def specs(self, dids, urns=None, full=True):
        """Fetch full MIoT spec instances by device id.

        ``urns`` may be omitted: the gateway resolves the urn from the did when
        an empty string is supplied (``o0O0O00.java:99-106``).
        """
        dids = list(dids)
        if urns is None:
            urns = [""] * len(dids)
        body = {"dids": dids, "urns": list(urns), "isGetFullInstance": bool(full)}
        return self.post("/appgateway/gspec/Gspec/get_instances_by_dids", body)

    def scene_icons(self):
        """The icon catalogue for manual scenes (``GetIconInfoList``)."""
        payload = self.post(
            "/appgateway/miot/appsceneservice/AppSceneService/GetIconInfoList",
            {})
        return payload if isinstance(payload, list) else []

    def home_members(self, home_id):
        """Who has access to this home (``/v2/homeroom/member_list``)."""
        return self.post("/v2/homeroom/member_list", {"home_id": str(home_id)})

    def rename_device(self, did, name, is_group=False):
        """Rename one device (``/device/rename``)."""
        return self.post("/device/rename", {"app_version": 1,
                                            "newName": name, "did": str(did),
                                            "is_group": bool(is_group)})

    def rename_home(self, home_id, home_owner, name, icon=None,
                    smart_room_background=None, smart_scene_background=None):
        """Rename a home (``/homeroom/ctrhome`` with ``type:set``).

        The gateway stores the whole home record, so the icon/backgrounds of the
        existing record must be sent back untouched; otherwise they reset.
        """
        body = {"id": str(home_id), "type": "set",
                "home_owner": int(home_owner), "name": name, "app_ver": 11}
        if icon is not None:
            body["icon"] = icon
        if smart_room_background is not None:
            body["smart_room_background"] = smart_room_background
        if smart_scene_background is not None:
            body["smart_scene_background"] = smart_scene_background
        return self.post("/homeroom/ctrhome", body)

    def add_room(self, home_owner, parent_id, name, background=""):
        """Create a room under a home (``/homeroom/addroom``)."""
        body = {"type": "addroom", "name": name,
                "home_owner": int(home_owner), "parent_id": str(parent_id),
                "background": background or "", "did": []}
        return self.post("/homeroom/addroom", body)

    def delete_rooms(self, room_ids, home_owner):
        """Delete rooms (``/homeroom/batchdelroom``)."""
        return self.post("/homeroom/batchdelroom",
                         {"ids": [str(item) for item in room_ids],
                          "home_owner": int(home_owner)})

    def move_devices_to_room(self, room_id, dids, home_owner):
        """Move devices into a room (``/homeroom/batch_device_to_room``)."""
        return self.post("/homeroom/batch_device_to_room",
                         {"id": str(room_id), "home_owner": int(home_owner),
                          "dids": [str(item) for item in dids]})

    def scene_order(self, home_id, owner_uid):
        """Saved manual-scene order per room (``GetLightingManualSceneOrder``).

        The reply is keyed by room id, with ``"-1"`` meaning "all rooms":
        ``{"home_page_display_ids": [...], "second_page_display_ids": [...]}``.
        """
        payload = self.post(
            "/appgateway/miot/appsceneservice/AppSceneService/GetLightingManualSceneOrder",
            {"home_id": int(home_id), "owner_uid": int(owner_uid)})
        if isinstance(payload, dict) and "display_order" in payload:
            payload = payload.get("display_order")
        if not isinstance(payload, dict):
            return {}
        return payload.get("-1") or {}

    def set_scene_order(self, home_id, room_id, owner_uid, scene_ids,
                        home_page=True):
        """Persist the manual-scene order

        (``/AppSceneService/SetLightingManualSceneOrder``, see _m_j/bw6.java):
        ``home_page_display_ids`` orders the cards on the home page,
        ``second_page_display_ids`` the ones behind \u300c\u66f4\u591a\u300d.
        """
        key = ("home_page_display_ids" if home_page
               else "second_page_display_ids")
        body = {"home_id": int(home_id), "room_id": int(room_id),
                "owner_uid": int(owner_uid),
                key: [int(str(item)) for item in scene_ids]}
        return self.post(
            "/appgateway/miot/appsceneservice/AppSceneService/SetLightingManualSceneOrder",
            body)

    def set_room(self, home_owner, room_id, name=None, icon=None,
                 background=None, add=None, delete=None, app_ver=11):
        """Create/update a room (``/homeroom/ctrroom``).

        ``type: "set"`` with an ``id`` renames/restyles that room; ``add`` /
        ``delete`` carry device ids moving into or out of it.  The app sends
        this as a form field, but the gateway accepts a JSON body here.
        """
        body = {"type": "set", "app_ver": int(app_ver)}
        if home_owner:
            body["home_owner"] = int(home_owner)
        if room_id:
            body["id"] = room_id
        if name is not None:
            body["name"] = name
        if icon is not None:
            body["icon"] = icon
        if background is not None:
            body["background"] = background
        if add:
            body["add"] = list(add)
        if delete:
            body["delete"] = list(delete)
        return self.post("/homeroom/ctrroom", body)

    def device_messages(self, did, msg_type=6, timestamp=0, limit=20):
        """Message history of one device (``/v2/message/v2/list``).

        ``timestamp`` is a paging cursor: the app passes "now" and walks back,
        but the server only answers when it is 0 -- that returns the newest
        messages, which is what a device page wants (measured 2026-11-21).
        """
        body = {"did": str(did), "type": int(msg_type or 6),
                "timestamp": int(timestamp or 0), "limit": int(limit),
                "force_read": False}
        return self.post("/v2/message/v2/list", body)

    def mark_messages_read(self, dids, home_id=0, home_owner=0):
        """Mark the messages of these devices read.

        The app sends per-home groups: ``{homes: [{dids, home_id, home_owner}]}``
        (MessageCenterV2Activity.java:235-260).  Messages without a device
        cannot be addressed this way, so they stay unread.
        """
        group = {"dids": [str(item) for item in dids if str(item)],
                 "home_id": int(home_id or 0), "home_owner": int(home_owner or 0)}
        return self.post("/v2/message/v2/batch_read_msg", {"homes": [group]})

    def delete_message(self, msg_id, msg_type=0, did=""):
        """Delete one message (``{msg_ids, type, did}``)."""
        return self.post("/v2/message/v2/delete",
                         {"msg_ids": [int(msg_id)], "type": int(msg_type or 0),
                          "did": str(did or "")})

    def messages(self, timestamp=None, limit=30):
        """Newest message per type (``/v2/message/v2/typelist``).

        ``timestamp`` is milliseconds; the app passes now.  The server answers
        ``{"messages": [...]}`` with one record per type, not the full log --
        the per-type history lives behind ``/v2/message/v2/list`` which needs a
        ``did``.
        """
        body = {"timestamp": int(timestamp or time.time() * 1000),
                "limit": int(limit)}
        return self.post("/v2/message/v2/typelist", body)

    def has_new_messages(self, begin_at=0):
        """``True`` when anything arrived after ``begin_at`` (unix seconds)."""
        return self.post("/v2/message/v2/check_new_msg",
                         {"begin_at": int(begin_at)})

    def scenes(self, home_id=None):
        body = {"home_id": home_id} if home_id else {}
        return self.post("/appgateway/miot/appsceneservice/AppSceneService/"
                         "GetSimpleSceneList", body)

    def scene_info(self, scene_id, home_id=None, owner_uid=None):
        """Full scene record, including the timer trigger's cron string.

        ``GetSimpleSceneList`` only says ``first_trigger.src == "timer"``; the
        actual cadence (``08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5``, cron
        ``0 45 8 * * * *``) only shows up here.
        """
        body = {"scene_id": str(scene_id), "value_format": 1, "app_version": 12}
        if owner_uid:
            body["owner_uid"] = str(owner_uid)
        if home_id:
            body["home_id"] = str(home_id)
        return self.post("/appgateway/miot/appsceneservice/AppSceneService/"
                         "GetSceneInfo", body)

    def edit_scene(self, payload):
        """Create or update a scene from the ``GetSceneInfo`` shape.

        Keep ``scene_id`` to update in place; drop it to create a copy (that is
        how a new timer is made when you already have one to clone).  The
        service also expects ``edit_from``/``app_version``/``owner_uid``.
        """
        return self.post("/appgateway/miot/appsceneservice/AppSceneService/Edit",
                         payload)

    def delete_scene(self, scene_id, home_id=None, owner_uid=None, del_from=1):
        """Delete a scene/automation (``del_from=1`` is what the app sends)."""
        body = {"del_from": int(del_from), "scene_id": str(scene_id)}
        if home_id:
            body["home_id"] = str(home_id)
        if owner_uid:
            body["owner_uid"] = str(owner_uid)
        return self.post("/appgateway/miot/appsceneservice/AppSceneService/Delete",
                         body)

    def set_scene_status(self, scene_id, enable, home_id=None, owner_uid=None):
        """Turn a scene/automation on or off (``SetSceneStatus``)."""
        body = {"scene_id": str(scene_id), "enable": bool(enable)}
        if home_id:
            body["home_id"] = str(home_id)
        if owner_uid:
            body["owner_uid"] = str(owner_uid)
        return self.post("/appgateway/miot/appsceneservice/AppSceneService/"
                         "SetSceneStatus", body)

    def run_scene(self, scene_id, home_id=None, scene_type=2, phone_id="mihome-pc",
                  owner_uid=None):
        """Execute a manual scene.

        The body is fussier than it looks -- measured 2026-11-21 against the
        live gateway:
          * ``scene_type=0`` -> ``-8 invaild scene_type``; the app sends
            ``CommonUsedScene.SPEC_SCENE_TYPE`` (2) for scenes that come from
            ``GetSimpleSceneList``.  ``1`` is the legacy type and answers
            "sceneInfo is empty".
          * ``phone_id`` is required (the app passes the device id it signs
            with); ``home_id``/``owner_uid`` make the gateway resolve a shared
            home's scene.
        """
        body = {"scene_id": str(scene_id), "scene_type": int(scene_type),
                "phone_id": phone_id or "mihome-pc"}
        if home_id:
            body["home_id"] = str(home_id)
        if owner_uid:
            body["owner_uid"] = int(owner_uid)
        return self.post("/appgateway/miot/appsceneservice/AppSceneService/"
                         "NewRunScene", body)


SUCCESS_CODES = (0, 1)
# Measured on a real bulb (yeelink.light.mbulb3, 2026-11-19): every accepted
# write answers ``code: 1``, while a genuinely bad value answers
# ``-704220043``.  Treating "anything but 0" as failure made every bulb action
# pop a bogus error even though the write landed.
NOT_READABLE_CODES = (-704030013,)


def is_success(code):
    """True when the device accepted the request."""
    try:
        return int(code) in SUCCESS_CODES
    except (TypeError, ValueError):
        return False


def property_records(payload):
    """Yield the per-property records of a /miotspec/prop/get|set reply.

    The live gateway is inconsistent about the shape, so all of these occur::

        [ {"did":..,"siid":..,"piid":..,"value":..,"code":0}, ... ]   # prop/get
        {"params": [ {...}, ... ]}                                  # prop/set
        {"did":.., "siid":.., "piid":..}                            # single record

    `CloudClient.post` already unwrapped the outer `result` envelope.
    """
    if isinstance(payload, list):
        for record in payload:
            if isinstance(record, dict):
                yield record
        return
    if isinstance(payload, dict):
        params = payload.get("params")
        if isinstance(params, list):
            for record in params:
                if isinstance(record, dict):
                    yield record
            return
        if "siid" in payload:
            yield payload


def first_code(payload, siid=None, piid=None):
    """Code of the matching record (or the first one), `-1` when absent."""
    for record in property_records(payload):
        if siid is not None and record.get("siid") != siid:
            continue
        if piid is not None and record.get("piid") != piid:
            continue
        return record.get("code")
    return -1


def fetch_spec_from_miot_spec_org(urn, timeout=DEFAULT_TIMEOUT):
    """Public fallback: ``GET https://miot-spec.org/miot-spec-v2/instance?type=<urn>``.

    Verified against the live site: ``?urn=`` answers 400, ``?type=`` answers 200
    with the full instance.  Also exposes :func:`fetch_spec_index`.
    """
    url = BASE_SPEC + "/miot-spec-v2/instance?" + urlencode({"type": urn})
    request = Request(url, headers={"User-Agent": "mihome-pc"})
    try:
        response = urlopen(request, timeout=timeout)
        return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, ValueError) as exc:
        raise CloudError("spec fetch failed for %s: %s" % (urn, exc))


def load_spec(urn, cache_dir, language=None, timeout=DEFAULT_TIMEOUT):
    """Fetch a spec instance and its i18n table, caching both on disk.

    Returns a parsed :class:`mihome_pc.xiaomi.spec.Spec`.  Labels come out in
    Chinese when the multiLanguage table has a ``zh_cn`` entry (most do), which
    is why the UI does not need its own translation table.

    Cached files are ``<urn with ':'->'_'>.json`` and ``...i18n.json`` so a
    stale cache can be deleted surgically.
    """
    from . import spec as spec_mod

    if not os.path.isdir(cache_dir):
        os.makedirs(cache_dir)
    stem = os.path.join(cache_dir, urn.replace(":", "_"))
    spec_path = stem + ".json"
    i18n_path = stem + ".i18n.json"

    if os.path.exists(spec_path):
        with open(spec_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = fetch_spec_from_miot_spec_org(urn, timeout=timeout)
        with open(spec_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    parsed = spec_mod.parse_spec(payload)

    table = None
    if os.path.exists(i18n_path):
        try:
            with open(i18n_path, encoding="utf-8") as handle:
                table = json.load(handle)
        except ValueError:
            table = None
    if table is None:
        try:
            table = fetch_spec_i18n(urn, timeout=timeout)
            with open(i18n_path, "w", encoding="utf-8") as handle:
                json.dump(table, handle, ensure_ascii=False)
        except CloudError:
            table = None                 # translations are a bonus, not a requirement

    preference = (language,) if language else spec_mod.I18N_LANGUAGE_PREFERENCE
    if table:
        spec_mod.apply_i18n(parsed, table, preference=preference)
    return parsed


def fetch_spec_index(timeout=120):
    """``model -> urn`` index for every published spec (~6.9 MB of JSON).

    ``GET https://miot-spec.org/miot-spec-v2/instances?status=all`` returns
    ``{"instances": [{status, model, version, type, ts}, ...]}``.  Useful before
    login: it maps a catalog model to its spec urn without the account.
    """
    url = BASE_SPEC + "/miot-spec-v2/instances?status=all"
    request = Request(url, headers={"User-Agent": "mihome-pc"})
    try:
        response = urlopen(request, timeout=timeout)
        payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, ValueError) as exc:
        raise CloudError("spec index fetch failed: %s" % exc)
    index = {}
    for entry in payload.get("instances") or ():
        model = entry.get("model")
        if model:
            index[model] = entry.get("type") or ""
    return index


def fetch_spec_i18n(urn, timeout=DEFAULT_TIMEOUT):
    """``GET /instance/v2/multiLanguage?urn=<urn>`` -> ``{"en": {...}, "zh_cn": {...}}``.

    Note the parameter name differs from the single-instance endpoint: this one
    takes ``urn=`` while ``/miot-spec-v2/instance`` takes ``type=``.  Verified
    against the live site (``?type=`` here answers 400).
    """
    url = BASE_SPEC + "/instance/v2/multiLanguage?" + urlencode({"urn": urn})
    request = Request(url, headers={"User-Agent": "mihome-pc"})
    try:
        response = urlopen(request, timeout=timeout)
        payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, ValueError) as exc:
        raise CloudError("spec i18n fetch failed for %s: %s" % (urn, exc))
    return payload.get("data") or {}
