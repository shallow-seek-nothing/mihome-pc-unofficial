"""Zero-dependency HTTP server + JSON API for the local web UI.

Design constraints that shaped this file:

* **Python 3.8 / stdlib only** -- ``http.server.ThreadingHTTPServer`` is enough
  for a handful of local requests; no framework, no WSGI server.
* **The UI is a browser tab (or a ``--app`` window)**, so the server must serve
  both static assets and JSON from one origin.  That kills CORS entirely.
* **Two data providers.**  ``DemoProvider`` renders from the spec fixtures so
  the UI can be developed and tested without an account; ``CloudProvider``
  talks to the real gateway.

Routing is a tiny explicit table rather than a regex router -- easier to read
and easy to unit test.
"""
import ipaddress
import json
import ntpath
import os
import socket
import threading
import time

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from ..domain import availability
from ..domain import labels
from ..domain import limits
from ..domain import model
from ..domain import model as model_mod
from ..domain import plugin_text
from ..domain import scene as scene_mod
from ..domain import timer as timer_mod
from ..domain import automation as automation_mod
from ..domain import message as message_mod
from ..domain import member as member_mod
from ..domain import favorites as favorites_mod
from ..xiaomi import auth, cloud, spec as spec_mod
import contextlib

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "static")
# POST body 上限：这个接口只接几十字节的 JSON，1 MiB 已经很宽松
MAX_BODY_BYTES = 1024 * 1024
# Demo specs ship inside the package (mihome_pc/assets/demo) so a fresh install
# shows a populated UI before the user logs in.  During development we fall back
# to the unit-test fixtures, which are the source of truth.
_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHIPPED_DEMO = os.path.join(_PACKAGE_DIR, "assets", "demo")
_TEST_FIXTURES = os.path.join(os.path.dirname(_PACKAGE_DIR), "tests", "fixtures", "specs")
FIXTURE_DIR = _SHIPPED_DEMO if os.path.isdir(_SHIPPED_DEMO) else _TEST_FIXTURES

_LOG_LOCK = threading.Lock()


def log_line(message):
    """Append to <data>/logs/web.log so a user can report what happened.

    The handler is silent on stdout (a pythonw process has none), so write
    failures need a file to land in or they are invisible.
    """
    try:
        directory = os.path.join(_data_dir(), "logs")
        if not os.path.isdir(directory):
            os.makedirs(directory)
        path = os.path.join(directory, "web.log")
        with _LOG_LOCK, open(path, "a", encoding="utf-8") as handle:
            handle.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

class Provider:
    """Interface the HTTP layer talks to."""

    demo = False

    def status(self):
        return {"logged_in": False, "demo": self.demo}

    def favorites(self):
        """Pinned device ids (persisted next to credentials)."""
        path = os.path.join(_data_dir(), "favorites.json")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {"dids": []}
        dids = data.get("dids") if isinstance(data, dict) else data
        return {"dids": [str(item) for item in (dids or [])]}

    def toggle_favorite(self, did):
        dids, pinned = favorites_mod.toggle(self.favorites()["dids"], did)
        path = os.path.join(_data_dir(), "favorites.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"dids": dids}, handle, ensure_ascii=False)
        except OSError as exc:
            log_line("save favorites failed: %s" % (exc,))
            return {"ok": False, "message": str(exc)[:160]}
        return {"ok": True, "pinned": pinned, "dids": dids}

    def _with_favorites(self, devices):
        """Pinned first, and mark which ones are pinned for the UI."""
        pinned = self.favorites()["dids"]
        marked = []
        for device in devices:
            item = dict(device)
            item["pinned"] = str(item.get("did")) in pinned
            marked.append(item)
        return favorites_mod.order(marked, pinned)

    def devices(self):
        return []

    def models(self):
        """Distinct device models in this account (empty for demo)."""
        return []

    def scenes(self):
        """Manual scenes of the current home (demo ships two fake ones)."""
        return {"home": {}, "scenes": [], "automations": [], "voice_count": 0}

    def refresh_scenes(self):
        """Scenes with the cache dropped (UI calls this after an edit)."""
        return self.scenes()

    def create_scene(self, name, did=None, kind="prop", siid=None, piid=None,
                     aiid=None, value=None, inputs=None, actions=None,
                     icon_url=None):
        """Create a manual scene (one or more actions)."""
        raise NotImplementedError

    def update_scene(self, scene_id, name=None, actions=None, icon_url=None):
        """Rename/re-arm an existing scene; ``{ok, message}``."""
        raise NotImplementedError

    def clone_automation(self, scene_id, name, actions=None):
        """Copy an automation (keeping its trigger) under a new name."""
        raise NotImplementedError

    def run_scene(self, scene_id):
        """Execute a manual scene; returns ``{ok, code, message}``."""
        raise NotImplementedError

    def set_scene_status(self, scene_id, enable):
        """Enable/disable an automation (timers live here)."""
        raise NotImplementedError

    def rename_device(self, did, name):
        """Rename a device; ``{ok, message}``."""
        raise NotImplementedError

    def move_scene(self, scene_id, direction):
        """Reorder a manual scene by one slot; ``{ok, message}``."""
        raise NotImplementedError

    def reorder_scenes(self, scene_ids):
        """Save a whole new scene order (drag & drop)."""
        raise NotImplementedError

    def scene_icons(self):
        """``[{id, url}]`` the scene editor offers."""
        return {"icons": []}

    def set_scene_icon(self, scene_id, icon_url):
        """Give a scene one of the catalogue icons."""
        raise NotImplementedError

    def rename_home(self, name):
        """Rename the current home; ``{ok, message}``."""
        raise NotImplementedError

    def add_room(self, name):
        """Create a room; returns ``{ok, room_id, message}``."""
        raise NotImplementedError

    def delete_room(self, room_id):
        """Delete a room (devices stay, they just lose the room)."""
        raise NotImplementedError

    def move_device(self, did, room_id):
        """Move one device into a room."""
        raise NotImplementedError

    def rename_room(self, room_id, name):
        """Rename one room; returns ``{ok, message}``."""
        raise NotImplementedError

    def device_info(self, did):
        """Facts about one device: ids, firmware, plugin text, last report."""
        raise NotImplementedError

    def messages(self):
        """Newest message per type; ``{"messages": [...], "unread": n}``."""
        return {"messages": [], "types": [], "unread": 0}

    def refresh_messages(self):
        return self.messages()

    def device_messages(self, did, limit=20):
        """Message history of one device; ``{messages, unread}``."""
        return {"messages": [], "types": [], "unread": 0}

    def members(self):
        """Home sharing members (read-only); ``{members, count}``."""
        return {"members": [], "count": 0}

    def unread_count(self):
        """Just the unread badge number (cheap enough to poll)."""
        return self.messages().get("unread", 0)

    def mark_messages_read(self):
        """Mark what is currently listed as read."""
        raise NotImplementedError

    def delete_message(self, msg_id):
        """Delete one message by id."""
        raise NotImplementedError

    def create_timer(self, did, hour, minute, name="", kind="prop",
                     siid=None, piid=None, aiid=None, value=None, inputs=None,
                     repeat="daily", days=None):
        """Create a new timer that runs one action on one device.

        ``repeat`` is ``daily`` / ``workday`` / ``holiday`` / ``weekly``; with
        ``weekly`` the ``days`` list (0 = Sunday) decides the cadence.
        """
        raise NotImplementedError

    def update_timer(self, scene_id, name=None, hour=None, minute=None):
        """Retime/rename a timer automation; returns ``{ok, scene_id, message}``."""
        raise NotImplementedError

    def delete_timer(self, scene_id):
        """Delete a timer automation."""
        raise NotImplementedError

    def meta(self):
        """Account / build facts for the UI's \u8d26\u53f7\u4e0e\u540c\u6b65 panel."""
        return {"demo": self.demo, "data_dir": _data_dir(),
                "plugins": plugin_coverage(),
                "provider": type(self).__name__,
                "credentials": os.path.isfile(
                    os.path.join(_data_dir(), "credentials.json")),
                "version": _app_version()}

    def rooms(self):
        """``{"home": name, "rooms": [{id, name, count}]}``."""
        return {"home": "", "rooms": []}

    def home_status(self):
        """Status popup payload; empty when unknown."""
        return {"total": 0, "groups": []}

    def wake_helper(self):
        """A speaker that can wake TVs, or ``None``."""
        return None

    def presence(self, did):
        """Is the device back on the network yet?

        Cloud ``isOnline`` lags behind reality by tens of seconds, so after a
        wake we check the LAN address directly: a TCP connect that is *refused*
        still proves the host is up and answering.
        """
        return {"reachable": None, "ip": "", "checked": False}

    def wake_tv(self, did):
        raise NotImplementedError

    def wake_lan(self, did):
        """Wake-on-LAN fallback: needs no speaker at all."""
        raise NotImplementedError

    def _plugin_texts(self, model_name):
        """Cached ``texts.json`` of a model's plugin (``{}`` when absent)."""
        if not model_name:
            return {}
        cache = getattr(self, "_texts_cache", None)
        if cache is None:
            cache = self._texts_cache = {}
        if model_name not in cache:
            cache[model_name] = plugin_text.load(
                model_name, os.path.join(_data_dir(), "plugins"))
        return cache[model_name]

    def state_seen(self, did):
        """``{"4.1": <unix seconds>}`` for values the device last reported.

        Offline devices still answer ``prop/get`` with the last value they
        pushed, tagged with ``updateTime``; the UI uses this to say how old it
        is instead of presenting a cached number as live.
        """
        return {}

    def view(self, did):
        raise KeyError(did)

    def set_property(self, did, siid, piid, value):
        raise NotImplementedError

    def availability(self, did):
        return None

    def run_action(self, did, siid, aiid, inputs=None):
        raise NotImplementedError


class DemoProvider(Provider):
    """Serves synthetic devices built from the bundled spec fixtures."""

    demo = True

    # (did, model, display name, fixture file, online)
    DEMO_DEVICES = [
        ("demo-airconditioner", "xiaomi.aircondition.ma1", "\u5ba2\u5385\u7a7a\u8c03",
         "airconditioner_ma1.json", True),
        ("demo-dishwasher", "xiaomi.dishwasher.s2", "\u53a8\u623f\u6d17\u7897\u673a",
         "dishwasher_s2.json", True),
        ("demo-washer", "minij.washer.v5", "\u6d17\u8863\u673a",
         "washer_v5.json", True),
        ("demo-fryer", "careli.fryer.maf04", "\u7a7a\u6c14\u70b8\u9505",
         "fryer_maf04.json", False),
        ("demo-healthpot", "xiaomi.health_pot.p1v2", "\u517b\u751f\u58f6",
         "health_pot_p1v2.json", True),
        # 电视是唯一「只有遥控面板」的设备：所有属性都归遥控面板，
        # 没有通用控件。它能演示音量/静音/输入源的当前值。
        ("demo-tv", "xiaomi.tv.fsv1", "\u7535\u89c6",
         "television_fsv1.json", True),
    ]

    def __init__(self):
        self._views = {}
        self._states = {}
        self._availability = {}
        self._room_of = {}          # filled by rooms(); devices() reads it
        self._load()
        # ``MIHOME_PC_DEMO_EMPTY=1`` starts with nothing: the cheapest way to
        # look at the empty states (fresh account before adding a device).
        flag = str(os.environ.get("MIHOME_PC_DEMO_EMPTY", ""))
        self.empty = flag not in ("", "0", "false", "False")
        if self.empty:
            self._views = {}
            self._states = {}

    def _load(self):
        for did, model_name, name, fixture, online in self.DEMO_DEVICES:
            path = os.path.join(FIXTURE_DIR, fixture)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as handle:
                parsed = spec_mod.parse_spec(json.load(handle))
            i18n_path = path[:-len(".json")] + ".i18n.json"
            if os.path.exists(i18n_path):
                with open(i18n_path, encoding="utf-8") as handle:
                    spec_mod.apply_i18n(parsed, json.load(handle).get("data") or {})
            self._views[did] = model.DeviceView(
                did=did, model=model_name, name=name, online=online, spec=parsed,
                catalog=model.load_catalog_entry(model_name),
                layout=model.load_card_layout(model_name),
                texts=self._plugin_texts(model_name))
            # seed state with plausible values from the spec
            state = {}
            for prop in parsed.readable_properties():
                if prop.value_list:
                    state[(prop.siid, prop.piid)] = prop.value_list[0].value
                elif prop.format == "bool":
                    state[(prop.siid, prop.piid)] = False
                elif prop.value_range:
                    state[(prop.siid, prop.piid)] = prop.value_range[0]
            self._states[did] = state

        # a hand-written blocker so the demo shows disabled controls
        self._availability["demo-dishwasher"] = availability.Availability(
            "xiaomi.dishwasher.s2",
            [availability.Blocker({"2.2": [6]}, ["2.4"],
                                  reason="\u5173\u673a\u72b6\u6001\u4e0b\u4e0d\u53ef\u8bbe\u7f6e\u6a21\u5f0f",
                                  source="manual")])
        # start the dishwasher in the "off" state to make the rule visible
        if "demo-dishwasher" in self._states:
            self._states["demo-dishwasher"][(2, 2)] = 6
        # a TV that looks alive: volume 12%, unmuted, input = TV
        if "demo-tv" in self._states:
            self._states["demo-tv"].update({(4, 1): 12, (4, 2): False, (2, 1): 1})

    DEMO_MESSAGES = [
        (9001, 6, "\u6d17\u6da4\u5b8c\u6210", "\u6d17\u8863\u673a", "demo-washer", 1),
        (9002, 13, "\u89c2\u5f71\u6a21\u5f0f\u6267\u884c\u5931\u8d25", "", "", 0),
    ]

    def scene_icons(self):
        return {"icons": [{"id": 1, "url": "https://example.invalid/a.png"},
                           {"id": 2, "url": "https://example.invalid/b.png"}]}

    def reorder_scenes(self, scene_ids):
        return {"ok": True,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u8c03\u6574\u987a\u5e8f"}

    def set_scene_icon(self, scene_id, icon_url):
        return {"ok": True,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u6362\u56fe\u6807"}

    def move_scene(self, scene_id, direction):
        return {"ok": True,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u8c03\u6574\u987a\u5e8f"}

    def rename_device(self, did, name):
        if did not in self._views:
            return {"ok": False,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u8bbe\u5907\u4e0d\u5b58\u5728"}
        if not (name or "").strip():
            return {"ok": False,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u540d\u5b57\u4e0d\u80fd\u4e3a\u7a7a"}
        return {"ok": True, "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u6539\u540d"}

    def rename_home(self, name):
        if not (name or "").strip():
            return {"ok": False,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u540d\u5b57\u4e0d\u80fd\u4e3a\u7a7a"}
        return {"ok": True, "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u6539\u540d"}

    def add_room(self, name):
        if not (name or "").strip():
            return {"ok": False, "message": "\u623f\u95f4\u540d\u4e0d\u80fd\u4e3a\u7a7a"}
        return {"ok": True, "room_id": "demo-room-new",
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u521b\u5efa"}

    def delete_room(self, room_id):
        return {"ok": True, "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u5220\u9664"}

    def move_device(self, did, room_id):
        if did not in self._views:
            return {"ok": False,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u8bbe\u5907\u4e0d\u5b58\u5728"}
        return {"ok": True, "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u79fb\u52a8"}

    def rename_room(self, room_id, name):
        if not (name or "").strip():
            return {"ok": False, "message": "\u623f\u95f4\u540d\u4e0d\u80fd\u4e3a\u7a7a"}
        return {"ok": True, "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u91cd\u547d\u540d"}

    def mark_messages_read(self):
        for index, item in enumerate(self.DEMO_MESSAGES):
            msg_id, kind, title, device, did, _ = item
            self.DEMO_MESSAGES[index] = (msg_id, kind, title, device, did, 0)
        return {"ok": True,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u6807\u4e3a\u5df2\u8bfb"}

    def delete_message(self, msg_id):
        before = len(self.DEMO_MESSAGES)
        self.DEMO_MESSAGES = [item for item in self.DEMO_MESSAGES
                              if str(item[0]) != str(msg_id).replace("demo-", "")]
        return {"ok": len(self.DEMO_MESSAGES) < before,
                "message": "\u6f14\u793a\u6a21\u5f0f"}

    def device_info(self, did):
        view = self._views.get(did)
        if view is None:
            raise KeyError(did)
        state = self._state_dict(did)
        rooms = getattr(self, "_room_of", {})
        return {"did": did, "name": view.name, "room": rooms.get(did, ""),
                "model": view.model, "urn": view.spec.urn if view.spec else "",
                "online": view.online, "localip": "",
                "mac": "AA:BB:CC:DD:EE:FF", "token": "demo12\u2026(24 hex)",
                "home": "\u6f14\u793a\u5bb6\u5ead",
                "info": {"manufacturer": "demo", "model": view.model,
                         "serial-number": "demo-sn",
                         "firmware-revision": "1.0.0"},
                "plugin": {"texts": 0},
                "last_report": int(time.time()) if state else 0,
                "presence": {"reachable": None, "ip": "", "checked": False}}

    def messages(self):
        if self.empty:
            return {"messages": [], "types": [], "unread": 0}
        now = int(time.time())
        # 走和云端一样的 normalise，避免两边字段不一致（types 就是这样漏掉的）
        rows = [{"msg_id": msg_id, "type": kind, "title": title,
                 "content": "", "did": did,
                 "ctime": now - msg_id % 3600, "is_new": is_new}
                for msg_id, kind, title, _device, did, is_new in self.DEMO_MESSAGES]
        names = {did: device for _msg, _kind, _title, device, did, _new
                 in self.DEMO_MESSAGES if did}
        return message_mod.normalise({"messages": rows}, names)

    DEMO_SCENES = [
        ("demo-scene-movie", "\u89c2\u5f71\u6a21\u5f0f", "lighting",
         ["\u5ba2\u5385\u7a7a\u8c03", "\u706f\u6ce1", "\u7535\u89c6"]),
        ("demo-scene-sleep", "\u7761\u7720\u6a21\u5f0f", "lighting",
         ["\u5ba2\u5385\u7a7a\u8c03", "\u706f\u6ce1"]),
    ]

    def scenes(self):
        if self.empty:
            return {"home": {"name": "\u6f14\u793a\u5bb6\u5ead"},
                    "scenes": [], "automations": [], "voice_count": 0}
        scenes = []
        for scene_id, name, category, devices in self.DEMO_SCENES:
            scenes.append({
                "id": scene_id, "name": name, "enabled": True,
                "category": category,
                "category_label": scene_mod.category_label(category),
                "icon": "", "updated": int(time.time()),
                "actions": [{"device": device, "did": "demo-" + str(index),
                              "model": "", "command": "set_properties",
                              "delay": 0}
                             for index, device in enumerate(devices)],
                "devices": list(devices),
            })
        return {"home": {"id": "demo-home", "name": "\u6f14\u793a\u5bb6\u5ead"},
                "scenes": scenes, "automations": [], "voice_count": 0}

    def run_scene(self, scene_id):
        known = {item[0] for item in self.DEMO_SCENES}
        if scene_id not in known:
            return {"ok": False, "code": -1,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u6ca1\u6709\u8fd9\u4e2a\u573a\u666f"}
        return {"ok": True, "code": 0,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u6a21\u62df\u6267\u884c"}

    def set_scene_status(self, scene_id, enable):
        known = {item[0] for item in self.DEMO_SCENES}
        if scene_id not in known:
            return {"ok": False, "code": -1,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u6ca1\u6709\u8fd9\u4e2a\u573a\u666f"}
        return {"ok": True, "code": 0,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u5207\u6362"}

    _NO_SPEC = ("\u6f14\u793a\u6a21\u5f0f\uff1a\u8bbe\u5907\u4e0d\u5b58\u5728"
                "\u6216\u6ca1\u6709\u52a8\u4f5c")

    def _demo_specs(self, did, actions):
        specs = actions or ([{"did": did}] if did else [])
        for spec in specs:
            if str(spec.get("did")) not in self._views:
                return None
        return specs

    def clone_automation(self, scene_id, name, actions=None):
        return {"ok": True, "scene_id": "demo-auto-new",
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u590d\u5236"}

    def update_scene(self, scene_id, name=None, actions=None, icon_url=None):
        known = {item[0] for item in self.DEMO_SCENES}
        if str(scene_id) not in known:
            return {"ok": False,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u6ca1\u6709\u8fd9\u4e2a\u573a\u666f"}
        return {"ok": True,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u4fdd\u5b58"}

    def create_scene(self, name, did=None, kind="prop", siid=None, piid=None,
                     aiid=None, value=None, inputs=None, actions=None,
                     icon_url=None):
        specs = self._demo_specs(did, actions)
        if not specs:
            return {"ok": False, "message": self._NO_SPEC}
        return {"ok": True, "scene_id": "demo-scene-new",
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u521b\u5efa"}

    def create_timer(self, did=None, hour=None, minute=None, name="",
                     kind="prop", siid=None, piid=None, aiid=None, value=None,
                     inputs=None, repeat="daily", days=None, actions=None):
        specs = self._demo_specs(did, actions)
        if not specs:
            return {"ok": False, "message": self._NO_SPEC}
        return {"ok": True, "scene_id": "demo-timer-new",
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u521b\u5efa"}

    def update_timer(self, scene_id, name=None, hour=None, minute=None):
        known = {item[0] for item in self.DEMO_SCENES}
        if scene_id not in known:
            return {"ok": False, "scene_id": str(scene_id),
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u6ca1\u6709\u8fd9\u4e2a\u573a\u666f"}
        return {"ok": True, "scene_id": str(scene_id), "message": ""}

    def delete_timer(self, scene_id):
        known = {item[0] for item in self.DEMO_SCENES}
        if scene_id not in known:
            return {"ok": False, "code": -1,
                    "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u6ca1\u6709\u8fd9\u4e2a\u573a\u666f"}
        return {"ok": True, "code": 0,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u5220\u9664"}

    def status(self):
        return {"logged_in": False, "demo": True,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff08\u672a\u767b\u5f55\uff09"}

    DEMO_ROOMS = {"\u5ba2\u5385": ["demo-airconditioner", "demo-tv"],
                  "\u53a8\u623f": ["demo-dishwasher", "demo-fryer",
                                    "demo-healthpot"],
                  "\u536b\u751f\u95f4": ["demo-washer"]}

    def rooms(self):
        if self.empty:
            self._room_of = {}
            return {"home": "\u6f14\u793a\u5bb6\u5ead", "rooms": []}
        mapping = {}
        for room, dids in self.DEMO_ROOMS.items():
            for did in dids:
                mapping[did] = room
        self._room_of = mapping
        return {"home": "\u6f14\u793a\u5bb6\u5ead",
                "rooms": [{"id": room, "name": room, "count": len(dids)}
                          for room, dids in self.DEMO_ROOMS.items()]}

    def devices(self):
        self.rooms()                            # fill the did -> room map
        result = []
        for did, view in sorted(self._views.items()):
            payload = view.to_dict()
            payload["state"] = self._state_dict(did)
            payload["blocked"] = self._blocked(did)
            payload["room"] = self._room_of.get(did, "")
            payload["last_seen"] = int(time.time()) if view.online else 0
            result.append(payload)
        return self._with_favorites(result)

    def view(self, did):
        view = self._views.get(did)
        if view is None:
            raise KeyError(did)
        payload = view.to_dict()
        payload["state"] = self._state_dict(did)
        payload["state_seen"] = self.state_seen(did)
        payload["blocked"] = self._blocked(did)
        return payload

    def _state_dict(self, did):
        return {"%d.%d" % key: value
                    for key, value in (self._states.get(did) or {}).items()}

    def _blocked(self, did):
        evaluator = self._availability.get(did)
        if evaluator is None:
            return {}
        state = self._states.get(did) or {}
        blocked = evaluator.evaluate(state)
        return {"%d.%d" % key: reason for key, (_, reason) in blocked.items()}

    def set_property(self, did, siid, piid, value):
        if did not in self._views:
            raise KeyError(did)
        evaluator = self._availability.get(did)
        state = self._states.setdefault(did, {})
        if evaluator is not None and not evaluator.is_enabled((siid, piid), state):
            return {"ok": False, "code": -5000,
                    "message": evaluator.reason_for((siid, piid), state)}
        prop = self._views[did].spec.property(siid, piid)
        if prop is None:
            return {"ok": False, "code": -1, "message": "unknown property"}
        try:
            state[(siid, piid)] = prop.coerce(value)
        except (TypeError, ValueError):
            return {"ok": False, "code": -1, "message": "bad value"}
        return {"ok": True, "code": 0, "state": self._state_dict(did)}

    def availability(self, did):
        evaluator = self._availability.get(did)
        return evaluator.to_dict() if evaluator else None

    def run_action(self, did, siid, aiid, inputs=None):
        if did not in self._views:
            raise KeyError(did)
        return {"ok": True, "code": 0,
                "message": "\u6f14\u793a\u6a21\u5f0f\uff1a\u5df2\u6a21\u62df\u6267\u884c"}


class CloudProvider(Provider):
    """Real devices: credentials from disk, specs cached per urn."""

    def __init__(self, credentials, client=None):
        self.credentials = credentials
        self.client = client or cloud.CloudClient(_session(credentials))
        self._devices = None
        self._views = {}
        self._states = {}
        self._availability = {}      # did -> evaluator (loaded lazily)
        # last device-side report time per property (``updateTime``), so the UI
        # can say "this value is 11 hours old" instead of pretending it is live
        self._state_at = {}
        self._rooms = None
        self._rooms_at = 0.0
        self._room_of = {}
        self._wake_helper = None
        self._devices_at = 0.0

    def status(self):
        return {"logged_in": True, "demo": False,
                "user_id": self.credentials.user_id,
                "base_url": self.client.base_url}

    def wake_helper(self):
        """Find an online Xiaoai speaker in this account.

        Speakers expose ``execute-text-directive`` (service 5 action 4), which is
        how Mi Home makes one act on a spoken sentence.  We use it to wake a TV
        that the cloud cannot reach directly.
        """
        if self._wake_helper:
            return self._wake_helper          # 只缓存命中结果，别把「没找到」缓存死
        best = None
        for record in self._load_devices():
            model = (record.get("model") or "").lower()
            if "speaker" not in model and "xiaoai" not in model:
                continue
            if not record.get("isOnline"):
                continue
            candidate = {"did": str(record.get("did")),
                         "name": record.get("name") or model,
                         "model": record.get("model")}
            best = candidate
            break
        self._wake_helper = best
        return best

    def presence(self, did):
        record = None
        for candidate in self._load_devices():
            if str(candidate.get("did")) == str(did):
                record = candidate
                break
        if record is None:
            return {"reachable": None, "ip": "", "checked": False}
        ip = record.get("localip") or ""
        if not ip:
            return {"reachable": None, "ip": "", "checked": False}
        reachable = False
        for port in (54321, 6466, 6095, 80):
            try:
                with socket.create_connection((ip, port), timeout=0.7):
                    reachable = True
                    break
            except ConnectionRefusedError:
                # 端口关着但主机在应答 —— 说明已经开机联网
                reachable = True
                break
            except OSError:
                continue
        return {"reachable": reachable, "ip": ip, "checked": True}

    def wake_lan(self, did):
        """Send a Wake-on-LAN magic packet to the device's WiFi MAC.

        Some Xiaomi TV models offer 「网络唤醒 / 远程唤醒」 in their own settings;
        when that is on, the set answers a magic packet even though it dropped off
        the network otherwise.  This path needs no speaker and no Bluetooth, so it
        is the fallback when an account has no Xiaoai device.
        """
        record = None
        for candidate in self._load_devices():
            if str(candidate.get("did")) == str(did):
                record = candidate
                break
        if record is None:
            return {"ok": False, "message": "\u627e\u4e0d\u5230\u8bbe\u5907"}
        mac = (record.get("mac") or "").replace("-", ":").lower()
        try:
            raw = bytes(int(part, 16) for part in mac.split(":"))
            if len(raw) != 6:
                raise ValueError(mac)
        except ValueError:
            return {"ok": False, "message": "MAC \u5730\u5740\u65e0\u6548: %s" % mac}

        packet = b"\xff" * 6 + raw * 16
        targets = [("255.255.255.255", 9), ("255.255.255.255", 7)]
        ip = record.get("localip") or ""
        if ip:
            targets.append((ip, 9))
        sent = 0
        for host, port in targets:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.sendto(packet, (host, port))
                    sent += 1
            except OSError:
                continue
        log_line("wol %s mac=%s sent=%d" % (did, mac, sent))
        self._devices = None
        self._devices_at = 0.0
        return {"ok": sent > 0, "mac": mac, "sent": sent,
                "message": "" if sent else "\u53d1\u9001\u5931\u8d25"}

    def wake_tv(self, did):
        helper = self.wake_helper()
        if not helper:
            return {"ok": False,
                    "message": "\u6ca1\u6709\u5728\u7ebf\u7684\u5c0f\u7231\u97f3\u7bb1"}
        # 电视的 turn-on 只在 ble-control 服务里；让音箱去执行
        text = "\u6253\u5f00\u7535\u89c6"
        # 唤醒后立即作废缓存，否则界面上永远是旧状态
        self._devices = None
        self._devices_at = 0.0
        self._views.pop(did, None)
        payload = self.client.execute_text_directive(helper["did"], text, True)
        code = payload.get("code", -1) if isinstance(payload, dict) else -1
        log_line("wake_tv %s via speaker %s code=%s" % (did, helper["did"], code))
        return {"ok": cloud.is_success(code), "code": code,
                "speaker": helper["name"],
                "text": text,
                "message": "" if cloud.is_success(code) else ("\u5931\u8d25 (code %s)" % code)}

    def home_status(self):
        """Fault / running / security, as computed by the cloud.

        The server returns one module per (category, device type); items carry
        ``hidden`` and a countdown, so the client only has to label them.
        """
        info = self.rooms()
        homes = []
        try:
            homes = (self.client.homes() or {}).get("homelist") or []
        except Exception:                       # noqa: BLE001
            return {"total": 0, "groups": []}
        if not homes:
            return {"total": 0, "groups": []}
        home = homes[0]
        owner = home.get("uid")
        try:
            payload = self.client.home_status(home.get("id"), owner)
        except Exception as exc:                # noqa: BLE001
            log_line("home_status failed: %s" % exc)
            return {"total": 0, "groups": []}

        names = {}
        for record in self._load_devices():
            names[str(record.get("did"))] = record.get("name") or record.get("model") or ""

        groups = []
        total = 0
        for module in payload.get("module_datas") or ():
            name = module.get("name") or ""
            kind = module.get("type") or ""
            devices = []
            for item in (module.get("data") or {}).get("device_list") or ():
                if item.get("hidden"):
                    continue
                did = str(item.get("did") or "")
                countdown = item.get("countdown_value") or ""
                text = ""
                if countdown not in ("", "0"):
                    text = "\u5269\u4f59 %s \u5206\u949f" % countdown
                elif name == "working":
                    text = "\u8fd0\u884c\u4e2d"
                devices.append({"did": did, "model": item.get("model") or "",
                                "name": names.get(did, did), "text": text,
                                "countdown": countdown})
            if not devices:
                continue
            total += len(devices)
            groups.append({
                "name": name,
                "label": labels.zh_status(name),
                "type": kind,
                "type_label": labels.zh_type(kind) if kind else "",
                "count": module.get("count") or len(devices),
                "devices": devices,
            })
        return {"total": total, "home": info.get("home") or "", "groups": groups}

    def rooms(self):
        """Rooms come from ``/v2/homeroom/gethome_merged`` (`roomlist[].dids`)."""
        fresh = (time.time() - (self._rooms_at or 0)) < self.DEVICES_TTL
        if self._rooms is not None and fresh:
            return self._rooms
        info = {"home": "", "rooms": []}
        try:
            payload = self.client.homes() or {}
        except Exception:                       # noqa: BLE001
            self._rooms = info
            return info
        homes = payload.get("homelist") or []
        if not homes:
            self._rooms = info
            return info
        home = homes[0]
        info["home"] = home.get("name") or ""
        self._home_uid = home.get("uid")      # rename/move need the owner
        self._home_name = home.get("name") or ""
        self._home_record = home              # rename keeps icon/background
        room_of = {}
        for room in home.get("roomlist") or ():
            name = room.get("name") or ""
            dids = room.get("dids") or []
            info["rooms"].append({"id": room.get("id"), "name": name,
                                  "count": len(dids)})
            for did in dids:
                room_of[str(did)] = name
        self._room_of = room_of
        self._rooms = info
        self._rooms_at = time.time()
        return info

    def _load_home(self):
        """The same home record ``rooms()`` caches (for rename payloads)."""
        self.rooms()
        return getattr(self, "_home_record", {}) or {}

    def _owner(self):
        return getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)

    ICONS_TTL = 600.0

    def scene_icons(self):
        fresh = (time.time() - (getattr(self, "_icons_at", 0) or 0)) < self.ICONS_TTL
        if getattr(self, "_icons", None) is not None and fresh:
            return self._icons
        try:
            raw = self.client.scene_icons()
        except Exception as exc:            # noqa: BLE001 - icons are optional
            log_line("scene icons failed: %s" % (exc,))
            raw = []
        icons = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("icon_url"):
                continue
            icons.append({"id": item.get("icon_id"),
                          "url": item.get("icon_url")})
        data = {"icons": icons}
        self._icons = data
        self._icons_at = time.time()
        return data

    def reorder_scenes(self, scene_ids):
        wanted = [str(item) for item in (scene_ids or []) if str(item)]
        current = self._manual_order()
        unknown = [item for item in wanted if item not in current]
        if unknown:
            return {"ok": False,
                    "message": "\u4e0d\u8ba4\u8bc6\u8fd9\u4e9b\u573a\u666f\uff1a%s"
                               % ", ".join(unknown[:3])}
        # keep any scene the caller did not mention at its old place
        ordered = wanted + [item for item in current if item not in wanted]
        if ordered == current:
            return {"ok": True, "message": ""}
        try:
            payload = self.client.set_scene_order(
                self._home_id(), -1, self._owner(), ordered, home_page=False)
        except Exception as exc:            # noqa: BLE001
            log_line("reorder scenes failed: %s" % (exc,))
            return {"ok": False, "message": str(exc)[:160]}
        self._scenes_at = 0
        log_line("reorder scenes -> %r (%r)" % (ordered, payload))
        return {"ok": True, "message": ""}

    def set_scene_icon(self, scene_id, icon_url):
        home_id = self._home_id()
        owner = self._owner()
        try:
            info = self.client.scene_info(scene_id, home_id, owner_uid=owner)
            payload = automation_mod.build_update_payload(
                info, home_id=home_id or "", owner_uid=owner or "",
                icon_url=str(icon_url or ""))
            result = self.client.edit_scene(payload)
        except Exception as exc:            # noqa: BLE001
            log_line("set scene icon %s failed: %s" % (scene_id, exc))
            return {"ok": False, "message": str(exc)[:200]}
        self._scenes_at = 0
        log_line("set scene icon %s -> %r: %r" % (scene_id, icon_url, result))
        return {"ok": True, "message": ""}

    def _manual_order(self):
        """Scene ids in the order the user arranged them (saved order first)."""
        data = self.scenes()
        listed = [scene["id"] for scene in data.get("scenes") or ()]
        try:
            saved = self.client.scene_order(self._home_id(), self._owner())
        except Exception as exc:            # noqa: BLE001 - order is cosmetic
            log_line("scene order failed: %s" % (exc,))
            saved = {}
        ordered = [str(item) for item in
                   (list(saved.get("home_page_display_ids") or [])
                    + list(saved.get("second_page_display_ids") or []))
                   if str(item) in listed]
        ordered += [item for item in listed if item not in ordered]
        return ordered

    def move_scene(self, scene_id, direction):
        ids = self._manual_order()
        if str(scene_id) not in ids:
            return {"ok": False, "message": "\u6ca1\u6709\u8fd9\u4e2a\u573a\u666f"}
        index = ids.index(str(scene_id))
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(ids):
            return {"ok": True, "message": "\u5df2\u5728\u8fb9\u4e0a"}
        ids[index], ids[target] = ids[target], ids[index]
        try:
            # The whole order goes to "second page": that list accepts any
            # length, while home_page_display_ids answers -6 when it is full.
            payload = self.client.set_scene_order(
                self._home_id(), -1, self._owner(), ids, home_page=False)
        except Exception as exc:            # noqa: BLE001
            log_line("set scene order failed: %s" % (exc,))
            return {"ok": False, "message": str(exc)[:160]}
        self._scenes_at = 0
        log_line("move scene %s %s -> %r" % (scene_id, direction, payload))
        return {"ok": True, "message": ""}

    def rename_device(self, did, name):
        clean = (name or "").strip()
        if not clean:
            return {"ok": False,
                    "message": "\u8bbe\u5907\u540d\u4e0d\u80fd\u4e3a\u7a7a"}
        if len(clean) > 30:
            return {"ok": False,
                    "message": "\u8bbe\u5907\u540d\u592a\u957f\uff08\u2264 30 \u5b57\uff09"}
        try:
            payload = self.client.rename_device(did, clean)
        except Exception as exc:            # noqa: BLE001
            log_line("rename device %s failed: %s" % (did, exc))
            return {"ok": False, "message": str(exc)[:160]}
        # DeviceView copies the name, so the built view must be dropped too --
        # otherwise /api/device keeps answering with the old name forever.
        self._views.pop(str(did), None)
        self._devices_at = 0
        self._scenes_at = 0                 # actions carry the old device name
        log_line("rename device %s -> %r: %r" % (did, clean, payload))
        return {"ok": True, "message": ""}

    def rename_home(self, name):
        clean = (name or "").strip()
        if not clean:
            return {"ok": False,
                    "message": "\u5bb6\u5ead\u540d\u4e0d\u80fd\u4e3a\u7a7a"}
        try:
            info = self._load_home()
        except Exception as exc:            # noqa: BLE001
            return {"ok": False, "message": str(exc)[:160]}
        try:
            payload = self.client.rename_home(
                info.get("id"), info.get("uid"), clean,
                icon=info.get("icon"),
                smart_room_background=info.get("smart_room_background"),
                smart_scene_background=info.get("smart_scene_background"))
        except Exception as exc:            # noqa: BLE001
            log_line("rename home failed: %s" % (exc,))
            return {"ok": False, "message": str(exc)[:160]}
        self._home_name = clean
        log_line("rename home -> %r: %r" % (clean, payload))
        return {"ok": True, "message": ""}

    def add_room(self, name):
        clean = (name or "").strip()
        if not clean:
            return {"ok": False,
                    "message": "\u623f\u95f4\u540d\u4e0d\u80fd\u4e3a\u7a7a"}
        if len(clean) > 20:
            return {"ok": False,
                    "message": "\u623f\u95f4\u540d\u592a\u957f\uff08\u2264 20 \u5b57\uff09"}
        home_id = self._home_id()
        try:
            payload = self.client.add_room(self._owner(), home_id, clean)
        except Exception as exc:            # noqa: BLE001
            log_line("add room %r failed: %s" % (clean, exc))
            return {"ok": False, "message": str(exc)[:160]}
        self._rooms_at = 0
        room_id = ""
        if isinstance(payload, dict):
            # the gateway answers {"roomid": "<room_id>", "did": []}
            room_id = str(payload.get("roomid") or payload.get("id")
                           or payload.get("room_id") or "")
        log_line("add room %r -> %r" % (clean, payload))
        return {"ok": True, "room_id": room_id, "message": ""}

    def delete_room(self, room_id):
        try:
            payload = self.client.delete_rooms([room_id], self._owner())
        except Exception as exc:            # noqa: BLE001
            log_line("delete room %s failed: %s" % (room_id, exc))
            return {"ok": False, "message": str(exc)[:160]}
        self._rooms_at = 0
        log_line("delete room %s -> %r" % (room_id, payload))
        return {"ok": True, "message": ""}

    def move_device(self, did, room_id):
        try:
            payload = self.client.move_devices_to_room(room_id, [did],
                                                       self._owner())
        except Exception as exc:            # noqa: BLE001
            log_line("move %s -> %s failed: %s" % (did, room_id, exc))
            return {"ok": False, "message": str(exc)[:160]}
        self._rooms_at = 0
        self._devices_at = 0                 # the device's room label changed
        log_line("move %s -> %s: %r" % (did, room_id, payload))
        return {"ok": True, "message": ""}

    def rename_room(self, room_id, name):
        clean = (name or "").strip()
        if not clean:
            return {"ok": False,
                    "message": "\u623f\u95f4\u540d\u4e0d\u80fd\u4e3a\u7a7a"}
        if len(clean) > 20:
            return {"ok": False,
                    "message": "\u623f\u95f4\u540d\u592a\u957f\uff08\u2264 20 \u5b57\uff09"}
        owner = getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)
        try:
            payload = self.client.set_room(owner, room_id, name=clean)
        except Exception as exc:            # noqa: BLE001
            log_line("rename room %s failed: %s" % (room_id, exc))
            return {"ok": False, "message": str(exc)[:160]}
        self._rooms_at = 0                  # chips must show the new name
        log_line("rename room %s -> %s: %r" % (room_id, clean, payload))
        return {"ok": True, "message": ""}

    INFO_PROPERTIES = ((1, 1), (1, 2), (1, 3), (1, 4))   # manufacturer/model/sn/fw

    def device_info(self, did):
        record = None
        for candidate in self._load_devices():
            if str(candidate.get("did")) == str(did):
                record = candidate
                break
        if record is None:
            raise KeyError(did)
        self.rooms()                        # fills the did -> room map
        # last_report 用已经轮询到的结果；没轮询过就是 0（详情页会先请求 /api/device，
        # 那次会把 state_seen 填上）。这里刻意不主动 refresh，避免每次开信息卡都打一轮请求。
        model = record.get("model") or ""
        info = {"manufacturer": "", "model": "", "serial-number": "",
                "firmware-revision": ""}
        try:
            payload = self.client.props([(did, siid, piid)
                                         for siid, piid in self.INFO_PROPERTIES])
        except Exception as exc:            # noqa: BLE001 - info is best effort
            log_line("device info props failed: %s" % (exc,))
            payload = []
        names = {1: "manufacturer", 2: "model", 3: "serial-number",
                 4: "firmware-revision"}
        for item in cloud.property_records(payload):
            if item.get("code") != 0:
                continue
            name = names.get(item.get("piid"))
            if name:
                info[name] = item.get("value")
        seen = self.state_seen(did) or {}
        token = str(record.get("token") or "")
        presence = self.presence(did)
        return {
            "did": str(did),
            "name": record.get("name") or "",
            "room": self._room_of.get(str(did), ""),
            "model": model,
            "urn": record.get("spec_type") or "",
            "online": bool(record.get("isOnline")),
            "localip": record.get("localip") or "",
            "mac": record.get("mac") or "",
            "token": (token[:6] + "\u2026" + "(%d hex)" % len(token)) if token else "",
            "home": getattr(self, "_home_name", ""),
            "info": info,
            "plugin": {"texts": _plugin_text_count(model)},
            "last_report": max(seen.values()) if seen else 0,
            "presence": presence,
        }

    MESSAGES_TTL = 20.0

    def messages(self):
        fresh = (time.time() - (getattr(self, "_messages_at", 0) or 0)) < self.MESSAGES_TTL
        if getattr(self, "_messages", None) is not None and fresh:
            return self._messages
        try:
            payload = self.client.messages()
        except Exception as exc:            # noqa: BLE001 - badge must not break the UI
            log_line("messages failed: %s" % (exc,))
            payload = {}
        names = {}
        for record in self._load_devices():
            names[str(record.get("did"))] = (record.get("name")
                                              or record.get("model") or "")
        data = message_mod.normalise(payload, names)
        self._messages = data
        self._messages_at = time.time()
        return data

    def refresh_messages(self):
        self._messages_at = 0
        return self.messages()

    def device_messages(self, did, limit=20):
        try:
            payload = self.client.device_messages(did, limit=limit)
        except Exception as exc:            # noqa: BLE001
            log_line("device messages %s failed: %s" % (did, exc))
            return {"messages": [], "types": [], "unread": 0}
        names = {}
        for record in self._load_devices():
            names[str(record.get("did"))] = (record.get("name")
                                              or record.get("model") or "")
        return message_mod.normalise(payload, names)

    def members(self):
        home_id = self._home_id()
        try:
            payload = self.client.home_members(home_id)
        except Exception as exc:            # noqa: BLE001 - sharing view is optional
            log_line("member list failed: %s" % (exc,))
            return {"members": [], "count": 0}
        return member_mod.normalise(payload, getattr(self, "_home_uid", None))

    def mark_messages_read(self):
        current = self.messages()
        dids = [item["did"] for item in current["messages"] if item["did"]]
        if not dids:
            return {"ok": False,
                    "message": "\u6ca1\u6709\u53ef\u5b9a\u4f4d\u7684\u8bbe\u5907\u6d88\u606f"}
        home_id = self._home_id()          # also fills _home_uid from the home
        owner = getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)
        try:
            payload = self.client.mark_messages_read(
                sorted(set(dids)), home_id or 0, owner or 0)
        except Exception as exc:            # noqa: BLE001
            log_line("mark read failed: %s" % (exc,))
            return {"ok": False, "message": str(exc)[:160]}
        self._messages_at = 0
        log_line("mark read %r -> %r" % (sorted(set(dids)), payload))
        return {"ok": bool(payload), "message": "",
                "marked": len(set(dids))}

    def delete_message(self, msg_id):
        current = self.messages()
        target = None
        for item in current["messages"]:
            if item["id"] == str(msg_id):
                target = item
        if target is None:
            return {"ok": False, "message": "\u6ca1\u6709\u8fd9\u6761\u6d88\u606f"}
        try:
            payload = self.client.delete_message(msg_id, target["type"],
                                                 target["did"])
        except Exception as exc:            # noqa: BLE001
            log_line("delete message %s failed: %s" % (msg_id, exc))
            return {"ok": False, "message": str(exc)[:160]}
        self._messages_at = 0
        log_line("delete message %s -> %r" % (msg_id, payload))
        return {"ok": payload is not False, "message": ""}

    SCENES_TTL = 15.0

    def _home_id(self):
        """Id of the first home, cached briefly (scenes need it)."""
        fresh = (time.time() - (getattr(self, "_home_at", 0) or 0)) < 60
        if getattr(self, "_home_id_value", None) and fresh:
            return self._home_id_value
        try:
            payload = self.client.homes() or {}
        except Exception:                   # noqa: BLE001 - scenes are optional
            return None
        homes = payload.get("homelist") or []
        home = homes[0] if homes else {}
        self._home_id_value = home.get("id")
        self._home_name = home.get("name") or ""
        self._home_uid = home.get("uid")
        self._home_at = time.time()
        return self._home_id_value

    def scenes(self):
        fresh = (time.time() - (getattr(self, "_scenes_at", 0) or 0)) < self.SCENES_TTL
        if getattr(self, "_scenes", None) is not None and fresh:
            return self._scenes
        home_id = self._home_id()
        try:
            payload = self.client.scenes(home_id)
        except Exception as exc:            # noqa: BLE001 - never break the UI
            log_line("scenes failed: %s" % (exc,))
            payload = {}
        home = {"id": home_id or "", "name": getattr(self, "_home_name", "")}
        details = {}
        if isinstance(payload, dict):
            owner = getattr(self, "_home_uid", None) or getattr(
                self.credentials, "user_id", None)
            for record in payload.get("auto_scene_info_list") or ():
                scene_id = str((record or {}).get("scene_id") or "")
                if not scene_id:
                    continue
                try:
                    details[scene_id] = self.client.scene_info(
                        scene_id, home_id, owner_uid=owner)
                except Exception as exc:        # noqa: BLE001 - detail is optional
                    log_line("scene_info %s failed: %s" % (scene_id, exc))
        data = scene_mod.normalise(payload, home=home, details=details)
        self._scenes = self._apply_scene_order(data)
        self._scenes_at = time.time()
        return self._scenes

    def _apply_scene_order(self, data):
        """Sort the manual scenes the way the user arranged them in the app.

        The cloud list keeps its own order, so the saved display order
        (``GetLightingManualSceneOrder``) is applied here; unknown ids keep
        their cloud position at the end.
        """
        scenes = list(data.get("scenes") or ())
        try:
            saved = self.client.scene_order(self._home_id(), self._owner())
        except Exception as exc:            # noqa: BLE001 - order is cosmetic
            log_line("scene order failed: %s" % (exc,))
            saved = {}
        ids = [str(item) for item in
               (list(saved.get("home_page_display_ids") or [])
                + list(saved.get("second_page_display_ids") or []))]
        if not ids:
            return data
        rank = {scene_id: index for index, scene_id in enumerate(ids)}
        ordered = sorted(scenes, key=lambda scene: rank.get(scene["id"],
                                                           len(ids)))
        data["scenes"] = ordered
        return data

    def refresh_scenes(self):
        self._scenes_at = 0
        return self.scenes()

    def run_scene(self, scene_id):
        # 成功时网关直接回 ``true``（不是 {code:0}），失败走 CloudError。
        home_id = self._home_id()
        owner = getattr(self, "_home_uid", None)
        if owner is None:
            owner = getattr(self.credentials, "user_id", None)
        phone_id = str(getattr(self.credentials, "device_id", "") or "mihome-pc")
        try:
            payload = self.client.run_scene(scene_id, home_id, phone_id=phone_id,
                                            owner_uid=owner)
        except Exception as exc:            # noqa: BLE001 - report, never crash
            log_line("run scene %s failed: %s" % (scene_id, exc))
            return {"ok": False, "code": -1, "message": str(exc)[:160]}
        log_line("run scene %s -> %r" % (scene_id, payload))
        return _scene_result(payload, "\u6267\u884c")

    def set_scene_status(self, scene_id, enable):
        home_id = self._home_id()
        owner = getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)
        try:
            payload = self.client.set_scene_status(
                scene_id, bool(enable), home_id, owner_uid=owner)
        except Exception as exc:            # noqa: BLE001
            log_line("scene status %s failed: %s" % (scene_id, exc))
            return {"ok": False, "code": -1, "message": str(exc)[:160]}
        self._scenes_at = 0                 # enabled state changed
        log_line("scene status %s enable=%s -> %r" % (scene_id, enable, payload))
        return _scene_result(payload, "\u5207\u6362")

    def _action_specs(self, spec):
        """One spec dict or a list of them -> a plain list (back-compat)."""
        if not spec:
            return []
        if isinstance(spec, dict):
            return [spec]
        return [item for item in spec if isinstance(item, dict)]

    def _action_params_reason(self, did, spec):
        """场景/定时编辑器不支持带参数的动作；有参数就拒绝。

        前端会过滤，但后端也得兜底：否则直接调 API 仍会把空参数
        动作写进场景，而真实云端是否接受还不知道。
        """
        # fail-closed：**判断不了**就不能放行。这里三个分支都要拒绝：
        # demo 不知道动作定义、请求了不存在的 siid/aiid、spec 拿不到。
        view = self._views.get(did)
        if view is None:
            try:
                self.view(did)
                view = self._views.get(did)
            except Exception as exc:        # noqa: BLE001
                log_line("scene action spec unavailable: %s"
                         % type(exc).__name__)
                return "无法读取设备动作定义，请刷新设备后重试"
        if view is None:
            return "无法读取设备动作定义，请刷新设备后重试"
        siid = int(spec.get("siid") or 0)
        aiid = int(spec.get("aiid") or 0)
        for control in view.actions:
            if control.siid == siid and control.aiid == aiid:
                if control.params:
                    return ("场景/定时编辑器暂不支持带参数的动作（%s）"
                            % (control.label or control.name))
                return ""               # 已确认是无参数动作
        return "设备没有这个动作 %d.%d，已停止创建" % (siid, aiid)

    def _build_actions(self, specs):
        """Turn ``[{did, kind, siid, piid|aiid, value|inputs}]`` into action records.

        Each record is a copy of a real action of the same command, so the
        server keeps accepting what it produced itself (see 0.6.5 notes).
        Returns ``(actions, error_message)``.
        """
        devices = {str(record.get("did")): record
                   for record in self._load_devices()}
        owner = self._owner()
        templates = {}
        actions = []
        for order, spec in enumerate(specs, start=1):
            did = str(spec.get("did") or "")
            record = devices.get(did)
            if record is None:
                return None, "\u8bbe\u5907\u4e0d\u5b58\u5728\uff1a%s" % did
            kind = spec.get("kind") or "prop"
            command = "action" if kind == "action" else "set_properties"
            if command == "action":
                blocked = self._action_params_reason(did, spec)
                if blocked:
                    return None, blocked
            if command not in templates:
                templates[command] = self._scene_template(
                    want_action=(command == "action"))
            _template, template_action, reason = templates[command]
            if not template_action:
                text = reason or "\u6ca1\u6709\u53ef\u7528\u4f5c\u6a21\u677f\u7684\u52a8\u4f5c"
                return None, text
            action = automation_mod.build_action_from(
                template_action, command, did, int(spec.get("siid") or 0),
                piid=spec.get("piid"), aiid=spec.get("aiid"),
                value=(list(spec.get("inputs") or [])
                       if kind == "action" else spec.get("value")),
                device_name=record.get("name") or record.get("model") or did,
                model=record.get("model") or "",
                uid=record.get("uid") or owner)
            action["order"] = order
            action["id"] = order
            actions.append(action)
        return actions, ""


    def create_scene(self, name, did=None, kind="prop", siid=None, piid=None,
                     aiid=None, value=None, inputs=None, actions=None,
                     icon_url=None):
        specs = self._action_specs(actions)
        if not specs and did:
            specs = [{"did": did, "kind": kind, "siid": siid, "piid": piid,
                      "aiid": aiid, "value": value, "inputs": inputs}]
        if not specs:
            return {"ok": False, "message": "\u81f3\u5c11\u9700\u8981\u4e00\u4e2a\u52a8\u4f5c"}
        home_id = self._home_id()
        owner = self._owner()
        try:
            built, error = self._build_actions(specs)
            if built is None:
                return {"ok": False, "code": "no_template", "message": error}
            template, _action, reason = self._scene_template()
            if template is None:
                return {"ok": False, "code": "no_template",
                        "message": reason or "\u6ca1\u6709\u53ef\u7528\u6a21\u677f"}
            payload = automation_mod.build_scene_payload(
                template, name, built, home_id=home_id or "",
                owner_uid=owner or "", icon_url=icon_url)
            result = self.client.edit_scene(payload)
        except Exception as exc:            # noqa: BLE001
            log_line("create scene failed: %s: %s" % (type(exc).__name__, exc))
            return {"ok": False, "code": "failed",
                    "message": ("\u521b\u5efa\u573a\u666f\u5931\u8d25\uff08%s\uff09"
                                "\uff0c\u8be6\u60c5\u89c1\u65e5\u5fd7")
                               % type(exc).__name__}
        self._scenes_at = 0
        new_id = ""
        if isinstance(result, dict):
            new_id = str(result.get("scene_id") or "")
        log_line("create scene %r -> %r" % (name, result))
        return {"ok": True, "scene_id": new_id,
                "message": "\u5df2\u521b\u5efa\u573a\u666f"}

    def update_scene(self, scene_id, name=None, actions=None, icon_url=None):
        specs = self._action_specs(actions)
        home_id = self._home_id()
        owner = self._owner()
        try:
            info = self.client.scene_info(scene_id, home_id, owner_uid=owner)
            built = None
            if specs:
                built, error = self._build_actions(specs)
                if built is None:
                    return {"ok": False, "message": error}
            payload = automation_mod.build_update_payload(
                info, name=name, actions=built, home_id=home_id or "",
                owner_uid=owner or "", icon_url=icon_url)
            result = self.client.edit_scene(payload)
        except Exception as exc:            # noqa: BLE001
            log_line("update scene %s failed: %s" % (scene_id, exc))
            return {"ok": False, "message": str(exc)[:200]}
        self._scenes_at = 0
        log_line("update scene %s -> %r" % (scene_id, result))
        return {"ok": True, "message": ""}

    def clone_automation(self, scene_id, name, actions=None):
        specs = self._action_specs(actions)
        home_id = self._home_id()
        owner = self._owner()
        try:
            info = self.client.scene_info(scene_id, home_id, owner_uid=owner)
            built = None
            if specs:
                built, error = self._build_actions(specs)
                if built is None:
                    return {"ok": False, "message": error}
            payload = automation_mod.build_clone_payload(
                info, name, actions=built, home_id=home_id or "",
                owner_uid=owner or "")
            result = self.client.edit_scene(payload)
        except Exception as exc:            # noqa: BLE001
            log_line("clone %s failed: %s" % (scene_id, exc))
            return {"ok": False, "message": str(exc)[:200]}
        self._scenes_at = 0
        new_id = ""
        if isinstance(result, dict):
            new_id = str(result.get("scene_id") or "")
        log_line("clone %s -> %r (%r)" % (scene_id, name, result))
        return {"ok": True, "scene_id": new_id,
                "message": "\u5df2\u590d\u5236"}

    def _scene_template(self, want_action=False):
        """拿一条真实场景（含一个真实动作）当骨架。

        返回 ``(template, action, reason)``：成功时 reason 为空，
        失败时 reason 是给用户看的中文原因（不含异常文本）。
        之所以不能返回 None：调用方会去解包，空账号会直接 TypeError。
        """
        home_id = self._home_id()
        owner = getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)
        try:
            payload = self.client.scenes(home_id) or {}
        except Exception as exc:            # noqa: BLE001
            log_line("scene template list failed: %s" % (exc,))
            return None, None, ("读取场景列表失败（可能是网络或权限问题）")
        candidates = []
        for key in ("auto_scene_info_list", "manual_scene_info_list"):
            for record in payload.get(key) or ():
                scene_id = str((record or {}).get("scene_id") or "")
                if scene_id:
                    candidates.append(scene_id)
        if not candidates:
            return None, None, ("账号里还没有任何场景，新建需要先在手机 App "
                                   "里建一条同类场景作为模板")
        wanted = "action" if want_action else "set_properties"
        first = None
        first_reason = ""
        for scene_id in candidates:
            try:
                info = self.client.scene_info(scene_id, home_id, owner_uid=owner)
            except Exception:               # noqa: BLE001 - try the next one
                first_reason = "读取场景详情失败"
                continue
            if first is None:
                first = (info, None)
            for action in (info.get("scene_action") or {}).get("actions") or ():
                command = (action.get("payload_json") or {}).get("command")
                if command == wanted:
                    return info, action, ""
        if first is None:
            return None, None, (first_reason or "读取场景详情失败")
        return first[0], None, ("账号里还没有 %s 动作可作模板" % wanted)

    def create_timer(self, did=None, hour=None, minute=None, name="",
                     kind="prop", siid=None, piid=None, aiid=None, value=None,
                     inputs=None, repeat="daily", days=None, actions=None):
        specs = self._action_specs(actions)
        if not specs and did:
            specs = [{"did": did, "kind": kind, "siid": siid, "piid": piid,
                      "aiid": aiid, "value": value, "inputs": inputs}]
        if not specs:
            return {"ok": False,
                    "message": "\u81f3\u5c11\u9700\u8981\u4e00\u4e2a\u52a8\u4f5c"}
        home_id = self._home_id()
        owner = self._owner()
        try:
            repeat_kind = "" if repeat in (None, "daily", "") else str(repeat)
            weekdays = list(days or []) if repeat_kind == "weekly" else None
            label = timer_mod.label_for(hour, minute, weekdays, repeat_kind)
            if not (name or "").strip():
                name = label            # e.g. "07:30 周一、周三"
            built, error = self._build_actions(specs)
            if built is None:
                return {"ok": False, "code": "no_template", "message": error}
            template, _action, reason = self._scene_template()
            if template is None:
                return {"ok": False, "code": "no_template",
                        "message": reason or "\u6ca1\u6709\u53ef\u7528\u6a21\u677f"}
            payload = automation_mod.build_payload(
                template, name, hour, minute, built, home_id=home_id or "",
                owner_uid=owner or "", label=label, days=weekdays,
                kind=repeat_kind)
            result = self.client.edit_scene(payload)
        except Exception as exc:            # noqa: BLE001
            log_line("create timer failed: %s: %s" % (type(exc).__name__, exc))
            return {"ok": False, "code": "failed",
                    "message": ("\u521b\u5efa\u5b9a\u65f6\u5931\u8d25\uff08%s\uff09"
                                "\uff0c\u8be6\u60c5\u89c1\u65e5\u5fd7")
                               % type(exc).__name__}
        self._scenes_at = 0
        new_id = ""
        if isinstance(result, dict):
            new_id = str(result.get("scene_id") or "")
        log_line("create timer %r -> %r" % (name, result))
        return {"ok": True, "scene_id": new_id,
                "message": "\u5df2\u521b\u5efa\u5b9a\u65f6"}

    def update_timer(self, scene_id, name=None, hour=None, minute=None):
        home_id = self._home_id()
        owner = getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)
        try:
            info = self.client.scene_info(scene_id, home_id, owner_uid=owner)
            payload = timer_mod.build_payload(
                info, name=name, hour=hour, minute=minute,
                home_id=home_id or "", owner_uid=owner or "", scene_id=scene_id)
            result = self.client.edit_scene(payload)
        except Exception as exc:            # noqa: BLE001
            log_line("update timer %s failed: %s" % (scene_id, exc))
            return {"ok": False, "scene_id": str(scene_id),
                    "message": str(exc)[:200]}
        self._scenes_at = 0
        new_id = str((result or {}).get("scene_id") or scene_id) \
            if isinstance(result, dict) else str(scene_id)
        log_line("update timer %s -> %r" % (scene_id, result))
        return {"ok": True, "scene_id": new_id, "message": ""}

    def delete_timer(self, scene_id):
        home_id = self._home_id()
        owner = getattr(self, "_home_uid", None) or getattr(
            self.credentials, "user_id", None)
        try:
            payload = self.client.delete_scene(scene_id, home_id, owner_uid=owner)
        except Exception as exc:            # noqa: BLE001
            log_line("delete timer %s failed: %s" % (scene_id, exc))
            return {"ok": False, "code": -1, "message": str(exc)[:200]}
        self._scenes_at = 0
        result = _scene_result(payload, "\u5220\u9664")
        log_line("delete timer %s -> %r" % (scene_id, payload))
        return result

    DEVICES_TTL = 20.0

    def _load_devices(self, force=False):
        """Cached device list with a short TTL.

        The list was cached forever, so after waking a TV the UI kept showing
        "离线" from the stale record even though the device was already back.
        """
        fresh = (time.time() - (self._devices_at or 0)) < self.DEVICES_TTL
        if self._devices is not None and fresh and not force:
            return self._devices
        payload = self.client.devices() or {}
        devices = []
        for key in ("list", "devices", "device_info"):
            value = payload.get(key)
            if isinstance(value, list):
                devices = value
                break
        self._devices = devices
        self._devices_at = time.time()
        return devices

    def meta(self):
        payload = Provider.meta(self)
        payload.update({"user_id": self.credentials.user_id,
                        "base_url": self.client.base_url,
                        "models": len(self.models())})
        return payload

    def models(self):
        found = {record.get("model") for record in self._load_devices()
                 if record.get("model")}
        return sorted(found)

    def devices(self):
        self.rooms()                            # populate the did -> room map
        result = []
        for record in self._load_devices():
            did = record.get("did")
            try:
                payload = self.view(did)
                payload["room"] = self._room_of.get(str(did), "")
                # 最后一次有数据的时间：离线卡片靠它说「上次在线什么时候」
                payload["last_seen"] = _newest_seen(payload.get("state_seen"))
                result.append(payload)
            except Exception:                      # noqa: BLE001 - one bad device must not hide the rest
                result.append({"did": did, "model": record.get("model"),
                               "name": (record.get("name")
                                        or model_mod.display_model(record.get("model"))),
                               "online": bool(record.get("isOnline")),
                               "last_seen": 0,
                               "error": "spec unavailable"})
        return self._with_favorites(result)

    def view(self, did):
        if did in self._views:
            view = self._views[did]
        else:
            record = None
            for candidate in self._load_devices():
                if candidate.get("did") == did:
                    record = candidate
                    break
            if record is None:
                raise KeyError(did)
            urn = record.get("spec_type") or ""
            if not urn:
                raise KeyError("device %s has no spec_type" % did)
            parsed = cloud.load_spec(urn, _spec_cache_dir())
            view = model.DeviceView(
                did=did, model=record.get("model") or "",
                name=(record.get("name")
                      or model_mod.display_model(record.get("model") or "")),
                online=bool(record.get("isOnline")), spec=parsed,
                catalog=model.load_catalog_entry(record.get("model") or ""),
                layout=model.load_card_layout(record.get("model") or ""),
                texts=self._plugin_texts(record.get("model") or ""))
            self._views[did] = view
        # Pull fresh values so the availability rules can be applied; without
        # this the UI would receive rules but no per-control `blocked` map.
        # a read failure must not hide the device
        with contextlib.suppress(Exception):
            self.refresh_state(did)
        payload = view.to_dict()
        payload["state"] = self._state_dict(did)
        payload["state_seen"] = self.state_seen(did)
        payload["blocked"] = self._blocked(did, view.model)
        # 有些属性的范围取决于另一个属性的当前值（空气炸锅：温度越高时长越短）
        state = self._states.get(did) or {}
        flat = {"%d.%d" % key: value for key, value in state.items()}
        for _bucket in ("groups", ):
            pass
        for group in payload.get("groups") or ():
            for control in group.get("writable") or ():
                override = limits.dynamic_range(view.model, control["address"], flat)
                if override:
                    control["range"] = override
                    control["limit_hint"] = limits.explain(view.model, control["address"], flat)
        for control in payload.get("status_list") or ():
            override = limits.dynamic_range(view.model, control["address"], flat)
            if override:
                control["range"] = override
        # 电视的开机键只存在于 ble-control 服务；账号里若有在线小爱音箱，
        # 可以借它执行「打开电视」把电视唤醒（实测可行）。
        if view.remote and (view.remote.get("roles") or {}).get("power_on"):
            payload["wake_via"] = self.wake_helper()
            for candidate in self._load_devices():
                if str(candidate.get("did")) == str(did):
                    payload["mac"] = candidate.get("mac") or ""
                    payload["localip"] = candidate.get("localip") or ""
                    break
        return payload

    def _blocked(self, did, model_name):
        """``{"2.3": reason}`` for controls the rules disable right now."""
        rules = load_availability(model_name=model_name)
        if not rules:
            return {}
        evaluator = availability.Availability.from_dict(rules)
        state = self._states.get(did) or {}
        blocked = evaluator.evaluate(state)
        return {"%d.%d" % key: reason for key, (_, reason) in blocked.items()}

    def _state_dict(self, did):
        return {"%d.%d" % key: value
                    for key, value in (self._states.get(did) or {}).items()}

    def state_seen(self, did):
        return {"%d.%d" % key: stamp
                for key, stamp in (self._state_at.get(did) or {}).items()}

    def refresh_state(self, did):
        view = self._views.get(did)
        if view is None:
            self.view(did)
            view = self._views[did]
        items = view.poll_items()
        if not items:
            return {}
        payload = self.client.props(items)
        state = self._states.setdefault(did, {})
        seen = self._state_at.setdefault(did, {})
        for record in cloud.property_records(payload):
            if record.get("code") != 0:
                continue
            key = (record["siid"], record["piid"])
            state[key] = record.get("value")
            if record.get("updateTime"):
                seen[key] = int(record["updateTime"])
        return self._state_dict(did)

    def set_property(self, did, siid, piid, value):
        """先在本地把能拒的全拒掉，最后才发一次云端请求。

        以前是先 `set_props()` 再检查：云端已经被调用一次，才告诉用户“不行”。
        """
        view = self._views.get(did)
        if view is None:
            self.view(did)
            view = self._views.get(did)
        state = self._states.setdefault(did, {})
        model_name = view.model if view is not None else ""
        evaluator = self._availability.get(did)
        if evaluator is None:
            evaluator = load_availability_evaluator(view, model_name)
            if evaluator is not None:
                self._availability[did] = evaluator
        coerced, error = validate_write(view, state, evaluator, model_name,
                                        siid, piid, value)
        if error is not None:
            code, why = error
            log_line("set %s %d.%d=%r rejected locally: %s"
                     % (did, siid, piid, value, why))
            return {"ok": False, "code": code, "state": self._state_dict(did),
                    "message": why}
        payload = self.client.set_props([(did, siid, piid, coerced)])
        code = None
        for record in cloud.property_records(payload):
            if record.get("siid") == siid and record.get("piid") == piid:
                code = record.get("code")
                break
        accepted = cloud.is_success(code)
        if accepted:
            # 存转换后的值，不存用户原始输入
            state[(siid, piid)] = coerced
        log_line("set %s %d.%d=%r code=%s accepted=%s"
                 % (did, siid, piid, value, code, accepted))
        return {"ok": accepted, "code": code,
                "state": self._state_dict(did),
                "message": "" if accepted else ("设备拒绝 (code %s)" % code)}

    def availability(self, did):
        return load_availability(view_model=self._views.get(did))

    def run_action(self, did, siid, aiid, inputs=None):
        """Send an action with its arguments in spec order.

        两种参数要分开处理：裸 iid 引用读当前属性值，inline 字面量取用户输入。
        参数不完整或不合法时直接拒绝，不调云端。
        """
        view = self._views.get(did)
        if view is None:
            self.view(did)
            view = self._views.get(did)
        control = None
        if view is not None:
            for action in view.actions:
                if action.siid == siid and action.aiid == aiid:
                    control = action
                    break
        if control is None:
            return {"ok": False, "code": -1, "args": [],
                    "message": "\u8bbe\u5907\u6ca1\u6709\u8fd9\u4e2a\u52a8\u4f5c %d.%d"
                               % (siid, aiid)}
        # 参数里有 inline 时才需要最新的属性值
        if control.in_refs:
            with contextlib.suppress(Exception):
                self.refresh_state(did)
        args, error = build_action_args(control, self._states.get(did, {}), inputs)
        if error is not None:
            log_line("action %s %d.%d rejected locally: %s"
                     % (did, siid, aiid, error))
            return {"ok": False, "code": -1, "args": [], "message": error}
        payload = self.client.call_action(did, siid, aiid, args)
        record = payload if isinstance(payload, dict) else {}
        code = record.get("code", -1)
        accepted = cloud.is_success(code)
        log_line("action %s %d.%d in=%r code=%s" % (did, siid, aiid, args, code))
        return {"ok": accepted, "code": code, "args": args,
                "message": "" if accepted else ("\u8bbe\u5907\u62d2\u7edd (code %s)" % code)}


def load_availability_evaluator(view_model=None, model_name=None):
    """返回可调用 `is_enabled()` 的评估器；写入路径需要它。

    `load_availability()` 返回的是 `.to_dict()` 后的字典（给 API 用），
    只能读不能判断，所以判断路径必须走这一个函数。
    """
    name = model_name or (view_model.model if view_model else "")
    if not name:
        return None
    path = os.path.join(_data_dir(), "availability", name + ".json")
    if not os.path.exists(path):
        return None
    return availability.Availability.load(path)


def load_availability(view_model=None, model_name=None):
    """Per-model availability rules saved by ``python -m mihome_pc probe``."""
    evaluator = load_availability_evaluator(view_model, model_name)
    return evaluator.to_dict() if evaluator else None


_TRUE_WORDS = ("1", "true", "on", "yes")
_FALSE_WORDS = ("0", "false", "off", "no")


def _strict_int(value):
    """严格整数：拒绝 bool、拒绝小数（1.5 不能被截成 1）。

    审阅意见：静默改写用户输入比直接报错更危险，所以这里不做任何宽容。
    """
    if isinstance(value, bool):
        raise ValueError("需要整数，不接受布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("需要整数，不接受小数")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        body = text[1:] if text[:1] in ("+", "-") else text
        if body.isdigit():
            return int(text)
        raise ValueError("需要整数")
    raise ValueError("需要整数")


def _strict_bool(value):
    """只接受明确的真/假表达；``"not-a-bool"`` 必须报错。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        raise ValueError("布尔参数只接受 0/1")
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        raise ValueError("布尔参数只接受 true/false")
    raise ValueError("布尔参数只接受 true/false")


def _coerce_inline(param, value):
    """校验并转换一个 inline 动作参数；不合法时抛 ValueError。"""
    fmt = (param.get("format") or "").strip()
    options = param.get("value_list")
    if options:
        allowed = [option[0] for option in options]
        number = _strict_int(value)
        if number not in allowed:
            raise ValueError("不在可选范围")
        return number
    if fmt == "bool":
        return _strict_bool(value)
    if fmt.startswith(("uint", "int")):
        number = _strict_int(value)
    elif fmt == "float":
        if isinstance(value, bool):
            raise ValueError("需要数字，不接受布尔值")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("需要数字")
    else:
        if not isinstance(value, str):
            raise ValueError("需要文本")
        return value
    span = param.get("value_range")
    if span and len(span) >= 2:
        low, high = span[0], span[1]
        if number < low or number > high:
            raise ValueError("超出范围 %s..%s" % (low, high))
    return number


def build_action_args(control, state, inputs):
    """按 spec 声明顺序组装动作参数；返回 ``(args, error)``。

    引用参数读当前属性值（没有值就报错，不发 None），
    inline 参数取用户输入并校验。
    """
    given = inputs if isinstance(inputs, dict) else {}
    args = []
    for param in control.params:
        piid = param.get("piid")
        if param.get("inline"):
            raw = given.get(str(piid), given.get(piid))
            if raw is None:
                return None, "缺少参数 %s" % (param.get("label") or piid)
            try:
                args.append(_coerce_inline(param, raw))
            except ValueError as exc:
                return None, "参数 %s 不合法：%s" % (
                    param.get("label") or piid, exc)
        else:
            value = state.get((control.siid, piid))
            if value is None:
                return None, "引用属性 %d.%d 还没有值" % (control.siid, piid)
            args.append(value)
    return args, None


def validate_write(view, state, evaluator, model_name, siid, piid, value):
    """写入前的全部本地校验，返回 ``(coerced, None)`` 或 ``(None, (code, message))``。

    顺序完全按外部审阅的要求：属性存在 → 可写 → 类型转换 → spec 接受
    → availability → 型号限制。任何一步失败都不会调用云端。
    """
    prop = view.spec.property(siid, piid) if view is not None else None
    if prop is None:
        return None, (-1, "unknown property %d.%d" % (siid, piid))
    if not prop.writable:
        return None, (-1, "property %d.%d is read-only" % (siid, piid))
    try:
        coerced = prop.coerce(value)
    except (TypeError, ValueError):
        return None, (-1, "bad value for %d.%d: %r" % (siid, piid, value))
    if not prop.accepts(coerced):
        return None, (-1, "%d.%d does not accept %r" % (siid, piid, coerced))
    if evaluator is not None and not evaluator.is_enabled((siid, piid), state):
        return None, (-5000, evaluator.reason_for((siid, piid), state))
    rejected = limits.check_value(model_name, "%d.%d" % (siid, piid), coerced,
                                  {"%d.%d" % key: v for key, v in state.items()})
    if rejected:
        return None, (-1, rejected[1])
    return coerced, None


def _data_dir():
    """Single source of truth: the CLI's resolver.

    This used to duplicate the logic and therefore disagreed with ``cli`` about
    where credentials/availability/specs live -- the web UI then silently saw no
    rules while the CLI wrote them somewhere else.
    """
    from ..cli import data_dir
    return data_dir()


def _session(credentials):
    return cloud.Session(user_id=credentials.user_id,
                         c_user_id=credentials.c_user_id,
                         service_token=credentials.service_token,
                         ssecurity=credentials.ssecurity,
                         device_id=credentials.device_id)


def _spec_cache_dir():
    directory = os.path.join(_data_dir(), "specs")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    return directory


# --------------------------------------------------------------------------
# QR login state machine (for the web UI)
# --------------------------------------------------------------------------

class LoginFlow:
    """Drives a QR login in the background so the page can poll for status."""

    def __init__(self, credentials_path):
        self.credentials_path = credentials_path
        self.state = "idle"          # idle|waiting|ok|error
        self.message = ""
        self.qr_png = None
        self.qr_url = None
        self._login = None
        self._thread = None
        self._generation = 0         # 作废「退出登录/重开弹窗」之前的扫码线程
        self.credentials = None

    def start(self):
        if self.state == "waiting":
            return self.state
        self.state = "waiting"
        self.message = ""
        self.qr_png = None
        self.qr_url = None
        self.credentials = None
        self._generation += 1
        generation = self._generation
        self._thread = threading.Thread(target=self._run, args=(generation,))
        self._thread.daemon = True
        self._thread.start()
        return self.state

    def reset(self):
        """Forget the finished login (used by logout / a fresh QR request).

        Bumps the generation so a scan that is still in flight cannot write
        credentials *after* the user logged out.
        """
        self._generation += 1
        self.state = "idle"
        self.message = ""
        self.qr_png = None
        self.qr_url = None
        self.credentials = None
        self._login = None
        return self.state

    def _run(self, generation):
        try:
            login = auth.QrLogin(qr_size=400)
            self._login = login
            qr_url, _lp, _lu = login.request_qr()
            self.qr_url = qr_url
            png = _fetch_image(login, qr_url)
            if generation != self._generation:
                return
            self.qr_png = png
            credentials = login.wait_for_scan(timeout=300)
            if generation != self._generation:
                return                 # logged out / restarted meanwhile
            auth.save_credentials(credentials, self.credentials_path)
            self.credentials = credentials
            self.state = "ok"
            self.message = "logged in"
        except Exception as exc:                   # noqa: BLE001 - surfaced to the page
            if generation != self._generation:
                return
            self.state = "error"
            # 只告诉页面错误类型：异常文本可能带 passToken / ticket / 响应体，
            # 而 /api/status 是不鉴权的（只限本机页面），不能把它当信使用。
            if isinstance(exc, auth.LoginTimeout):
                self.message = "LoginTimeout: 扫码登录超时，请重试"
            else:
                self.message = "%s: 登录失败（详情见日志）" % type(exc).__name__
            log_line("login failed: %s" % type(exc).__name__)

    def status(self):
        return {"state": self.state, "message": self.message,
                "has_image": bool(self.qr_png)}


def _fetch_image(login, url):
    try:
        response = login.transport._opener.open(url, timeout=20)
        return response.read()
    except Exception:                              # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "mihome-pc/0.1"

    # injected by AppServer
    provider_state = None      # {"provider", "stamp", "follow", "lock"}
    login_flow = None

    @property
    def provider(self):
        """Current provider, swapping in the cloud one once a login lands.

        ``follow`` says "this server owns the provider and keeps watching the
        credentials file"; tests pass an explicit provider and leave it False,
        so they never touch the network.

        Issue #1 (2026-11-24): this used ``stamp is None`` to mean "do not
        follow", but a *fresh install* has no credentials file, so its stamp is
        ``None`` too.  The first QR login therefore wrote the file while the
        property kept returning the demo provider forever -- users had to
        restart the process even though the docs promised otherwise.  Now the
        intent is a separate boolean and ``None`` only means "file absent".
        """
        state = self.provider_state
        if state is None:
            return DemoProvider()
        if not state.get("follow"):
            return state["provider"]
        stamp = _credentials_stamp(self.credentials_path)
        if stamp == state.get("stamp"):
            return state["provider"]
        lock = state.get("lock")
        with lock:            # two requests can notice the change at once
            stamp = _credentials_stamp(self.credentials_path)
            if stamp == state.get("stamp"):
                return state["provider"]
            provider = make_provider(self.credentials_path)
            state["provider"] = provider
            state["stamp"] = stamp
            log_line("provider -> %s (credentials %s)" % (
                "demo" if getattr(provider, "demo", True) else "cloud",
                "present" if stamp is not None else "absent"))
        sync_plugins_in_background(provider)
        return provider
    quiet = True

    def log_message(self, fmt, *args):            # noqa: A003 - stdlib signature
        if not self.quiet:
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    # -- helpers -------------------------------------------------------
    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    # -- account -------------------------------------------------------
    def _logout(self):
        """Drop the stored credentials and go back to demo data.

        Only this app's own file is touched: the account is the same one the
        phone's Mi Home app uses, and revoking the passToken server-side would
        sign the user out there too.  Every later request sees the file gone
        (see ``provider``) and swaps back to the demo provider on its own.
        """
        path = self.credentials_path
        state = self.provider_state
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            log_line("logout: cannot remove credentials: %s" % (exc,))
            return {"ok": False, "demo": False,
                    "error": "无法删除凭据文件：%s" % (getattr(exc, "strerror", None) or exc)}
        if state is not None and state.get("follow"):
            with state["lock"]:
                state["provider"] = DemoProvider()
                state["stamp"] = _credentials_stamp(path)
        if self.login_flow is not None:
            self.login_flow.reset()          # a scan in flight must not log back in
        log_line("logout: credentials removed")
        return {"ok": True, "demo": True}

    def _static(self, relative):
        """只能读 STATIC_DIR 里的文件。

        这个服务没有鉴权，路径必须自己守住：只拒绝 `..` 不够，因为 Windows 上
        `os.path.join(root, 'C:/x')` 会丢掉 root（带盘符的第二段被当成绝对路径）。
        所以：先解码一次 → 拒绝所有绝对/盘符/UNC/NUL 形式 → 最后用 realpath + commonpath
        确认结果确实在目录内（进程符号链接 / junction 也绕不过去）。
        """
        root = os.path.realpath(STATIC_DIR)
        try:
            safe = unquote(relative, errors="strict").replace("\\", "/")
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "bad path"}, 400)
            return
        if not safe:
            safe = "index.html"
        # ntpath.splitdrive 在 POSIX 上也能识别 `C:` / `\\\\host\\share`
        invalid = ("\x00" in safe or safe.startswith("/")
                   or bool(ntpath.splitdrive(safe)[0]) or os.path.isabs(safe))
        if invalid:
            self._json({"error": "bad path"}, 400)
            return
        path = os.path.realpath(os.path.join(root, safe))
        try:
            contained = os.path.commonpath([root, path]) == root
        except ValueError:            # 跨盘符会抛这个，直接当拒绝
            contained = False
        if not contained:
            self._json({"error": "bad path"}, 400)
            return
        if not os.path.isfile(path):
            self._json({"error": "not found", "path": safe}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as handle:
            self._send(200, handle.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    # -- request validation -------------------------------------------
    @staticmethod
    def _parse_host(value):
        """'[::1]:8080' -> ('::1', '8080')、'127.0.0.1' -> ('127.0.0.1', '')。"""
        text = (value or "").strip()
        if not text or "@" in text or "/" in text or "\\" in text:
            return None
        if text.startswith("["):
            end = text.find("]")
            if end < 0:
                return None
            name = text[1:end]
            rest = text[end + 1:]
            if rest and not rest.startswith(":"):
                return None
            port = rest[1:] if rest else ""
        else:
            if text.count(":") > 1:      # 不带括号的 IPv6 不合法
                return None
            name, _sep, port = text.partition(":")
        if port and not port.isdigit():
            return None
        if not name:
            return None
        return name, port

    @staticmethod
    def _is_loopback_name(name):
        if (name or "").strip().lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(name.strip()).is_loopback
        except ValueError:
            return False

    def _reject(self, status, message):
        self._json({"error": message}, status)
        return False

    def _request_allowed(self, write=False):
        """只对本机页面开放。

        这个服务没有鉴权，只绑 127.0.0.1；但浏览器里的其他站点仍可以
        通过 DNS rebinding 或跳过 CORS 的简单请求去碰它，不能只靠绑定地址。
        Host / Origin / Sec-Fetch-Site 三道校验就是为了把它关在本页面里。
        """
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return self._reject(403, "bad Host")
        parsed = self._parse_host(hosts[0])
        if parsed is None:
            return self._reject(403, "bad Host")
        host_name, host_port = parsed
        listen_port = self.server.server_address[1]
        if host_port and int(host_port) != listen_port:
            return self._reject(403, "bad Host")
        if not self._is_loopback_name(host_name):
            return self._reject(403, "bad Host")

        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1:
            return self._reject(403, "bad Origin")
        if origins:
            origin = origins[0].strip()
            if origin.lower() == "null":
                return self._reject(403, "bad Origin")
            # `urlsplit` 与 `.port` 都会对恶意输入抛 ValueError
            # （`http://[::1`、`http://h:bad`、`http://h:99999`），这里必须当拒绝，
            # 不能让它变成一个断开的连接（那样反而看不出是被拒绝还是出错）。
            try:
                parts = urlsplit(origin)
                origin_host = (parts.hostname or "").lower()
                origin_port = parts.port or 80
            except ValueError:
                return self._reject(403, "bad Origin")
            if parts.scheme != "http" or parts.username or parts.password:
                return self._reject(403, "bad Origin")
            if parts.path not in ("", "/") or parts.query or parts.fragment:
                return self._reject(403, "bad Origin")
            if origin_host != host_name.lower():
                return self._reject(403, "bad Origin")
            if origin_port != listen_port:
                return self._reject(403, "bad Origin")

        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            return self._reject(403, "cross-site request")
        return True

    def _read_json(self):
        """读 POST body：返回 (data, None) 或 (None, (status, message))。

        严格的理由：这个接口能改设备状态，不能接受一个恰好
        长得像表单的跨站请求（`text/plain` 能免预检）。
        """
        if self.headers.get("Transfer-Encoding"):
            return None, (400, "unsupported Transfer-Encoding")
        # 类型校验必须在所有 Content-Length 提前返回之前：
        # 否则「无 Content-Length + text/plain」会从“没有 body”那个分支漏过去。
        media = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media != "application/json":
            return None, (415, "Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}, None
        raw_length = raw_length.strip()
        if not raw_length.isdigit():
            return None, (400, "bad Content-Length")
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            return None, (413, "body too large")
        if length == 0:
            return {}, None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, (400, "bad JSON body")
        if not isinstance(data, dict):
            return None, (400, "JSON body must be an object")
        return data, None

    # -- routes --------------------------------------------------------
    def do_GET(self):                              # noqa: N802 - stdlib signature
        if not self._request_allowed():
            return
        path = self.path.split("?")[0]
        query = _parse_query(self.path)
        try:
            if path == "/" or path == "/index.html":
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path == "/api/status":
                provider = self.provider
                payload = provider.status()
                # 「为什么是演示模式」要能一眼看出来：没登录 / 被换掉了 /
                # 凭据文件坏了，三种情况的处置完全不同。
                payload["provider"] = type(provider).__name__
                payload["credentials"] = _credentials_stamp(self.credentials_path) is not None
                if payload.get("demo") and payload["credentials"]:
                    try:
                        auth.load_credentials(self.credentials_path)
                    except (OSError, ValueError) as exc:
                        payload["credentials_error"] = (
                            "凭据文件无法读取（%s），请重新扫码登录" % type(exc).__name__)
                        log_line("status: credentials unreadable: %s" % (exc,))
                payload["login"] = self.login_flow.status() if self.login_flow else None
                self._json(payload)
            elif path == "/api/devices":
                payload = self.provider.rooms()
                payload["devices"] = self.provider.devices()
                self._json(payload)
            elif path == "/api/device":
                did = query.get("did")
                if not did:
                    self._json({"error": "did required"}, 400)
                    return
                payload = self.provider.view(did)
                payload["availability"] = self.provider.availability(did)
                self._json(payload)
            elif path == "/api/availability":
                did = query.get("did")
                self._json({"availability": self.provider.availability(did)})
            elif path == "/api/presence":
                self._json(self.provider.presence(query.get("did") or ""))
            elif path == "/api/home-status":
                self._json(self.provider.home_status())
            elif path == "/api/device/info":
                did = query.get("did")
                if not did:
                    self._json({"error": "did required"}, 400)
                    return
                self._json(self.provider.device_info(did))
            elif path == "/api/favorites":
                self._json(self.provider.favorites())
            elif path == "/api/scene/icons":
                self._json(self.provider.scene_icons())
            elif path == "/api/members":
                self._json(self.provider.members())
            elif path == "/api/messages/unread":
                self._json({"unread": self.provider.unread_count()})
            elif path == "/api/messages/device":
                did = query.get("did")
                if not did:
                    self._json({"error": "did required"}, 400)
                    return
                try:
                    limit = int(query.get("limit") or 20)
                except ValueError:
                    limit = 20
                self._json(self.provider.device_messages(did, limit=limit))
            elif path == "/api/messages":
                if query.get("refresh"):
                    self._json(self.provider.refresh_messages())
                else:
                    self._json(self.provider.messages())
            elif path == "/api/meta":
                self._json(self.provider.meta())
            elif path == "/api/scenes":
                if query.get("refresh"):
                    self._json(self.provider.refresh_scenes())
                else:
                    self._json(self.provider.scenes())
            elif path == "/api/qr":
                if self.login_flow is None:
                    self._json({"error": "login not available"}, 400)
                    return
                self.login_flow.start()
                self._json(self.login_flow.status())
            elif path == "/api/qr/status":
                self._json(self.login_flow.status() if self.login_flow else {"state": "idle"})
            elif path == "/api/qr/image":
                if self.login_flow is None or not self.login_flow.qr_png:
                    self._json({"error": "no image"}, 404)
                    return
                self._send(200, self.login_flow.qr_png, "image/png")
            else:
                self._json({"error": "not found"}, 404)
        except KeyError as exc:
            self._json({"error": "not found: %s" % exc}, 404)
        except cloud.AuthError as exc:
            self._json({"error": str(exc), "auth": True}, 401)
        except Exception as exc:                   # noqa: BLE001
            log_line("GET %s failed: %s: %s" % (path, type(exc).__name__, exc))
            self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):                             # noqa: N802 - stdlib signature
        if not self._request_allowed(write=True):
            return
        path = self.path.split("?")[0]
        body, body_error = self._read_json()
        if body_error is not None:
            self._json({"error": body_error[1]}, body_error[0])
            return
        try:
            if path == "/api/logout":
                self._json(self._logout())
            elif path == "/api/set":
                did = body.get("did")
                siid = body.get("siid")
                piid = body.get("piid")
                if did is None or siid is None or piid is None:
                    self._json({"error": "did/siid/piid required"}, 400)
                    return
                result = self.provider.set_property(did, int(siid), int(piid),
                                                    body.get("value"))
                self._json(result)
            elif path == "/api/wol":
                did = body.get("did")
                if not did:
                    self._json({"error": "did required"}, 400)
                    return
                self._json(self.provider.wake_lan(did))
            elif path == "/api/wake-tv":
                did = body.get("did")
                if not did:
                    self._json({"error": "did required"}, 400)
                    return
                self._json(self.provider.wake_tv(did))
            elif path == "/api/action":
                did = body.get("did")
                siid = body.get("siid")
                aiid = body.get("aiid")
                if did is None or siid is None or aiid is None:
                    self._json({"error": "did/siid/aiid required"}, 400)
                    return
                self._json(self.provider.run_action(did, int(siid), int(aiid),
                                                  inputs=body.get("inputs")))
            elif path == "/api/messages/read":
                self._json(self.provider.mark_messages_read())
            elif path == "/api/messages/delete":
                msg_id = body.get("msg_id") or body.get("id")
                if not msg_id:
                    self._json({"error": "msg_id required"}, 400)
                    return
                self._json(self.provider.delete_message(str(msg_id)))
            elif path == "/api/favorites/toggle":
                did = body.get("did")
                if not did:
                    self._json({"error": "did required"}, 400)
                    return
                self._json(self.provider.toggle_favorite(str(did)))
            elif path == "/api/scene/reorder":
                ids = body.get("scene_ids") or body.get("ids")
                if not isinstance(ids, list) or not ids:
                    self._json({"error": "scene_ids required"}, 400)
                    return
                self._json(self.provider.reorder_scenes(ids))
            elif path == "/api/scene/icon":
                scene_id = body.get("scene_id") or body.get("id")
                if not scene_id:
                    self._json({"error": "scene_id required"}, 400)
                    return
                self._json(self.provider.set_scene_icon(
                    str(scene_id), body.get("icon_url") or ""))
            elif path == "/api/scene/move":
                scene_id = body.get("scene_id") or body.get("id")
                direction = body.get("direction")
                if not scene_id or direction not in ("up", "down"):
                    self._json({"error": "scene_id and direction(up|down) required"}, 400)
                    return
                self._json(self.provider.move_scene(str(scene_id), direction))
            elif path == "/api/device/rename":
                did = body.get("did")
                name = body.get("name")
                if not did or name is None:
                    self._json({"error": "did/name required"}, 400)
                    return
                self._json(self.provider.rename_device(str(did), name))
            elif path == "/api/home/rename":
                name = body.get("name")
                if name is None:
                    self._json({"error": "name required"}, 400)
                    return
                self._json(self.provider.rename_home(name))
            elif path == "/api/room/add":
                name = body.get("name")
                if name is None:
                    self._json({"error": "name required"}, 400)
                    return
                self._json(self.provider.add_room(name))
            elif path == "/api/room/delete":
                room_id = body.get("room_id") or body.get("id")
                if not room_id:
                    self._json({"error": "room_id required"}, 400)
                    return
                self._json(self.provider.delete_room(room_id))
            elif path == "/api/room/move":
                did = body.get("did")
                room_id = body.get("room_id") or body.get("id")
                if not did or not room_id:
                    self._json({"error": "did/room_id required"}, 400)
                    return
                self._json(self.provider.move_device(str(did), room_id))
            elif path == "/api/room/rename":
                room_id = body.get("room_id") or body.get("id")
                name = body.get("name")
                if not room_id or name is None:
                    self._json({"error": "room_id/name required"}, 400)
                    return
                self._json(self.provider.rename_room(room_id, name))
            elif path == "/api/automation/clone":
                scene_id = body.get("scene_id") or body.get("id")
                name = body.get("name")
                if not scene_id or not name:
                    self._json({"error": "scene_id/name required"}, 400)
                    return
                specs, error = _parse_actions(body)
                if error:
                    self._json({"error": error}, 400)
                    return
                self._json(self.provider.clone_automation(
                    str(scene_id), name, actions=specs or None))
            elif path == "/api/scene/update":
                scene_id = body.get("scene_id") or body.get("id")
                if not scene_id:
                    self._json({"error": "scene_id required"}, 400)
                    return
                specs, error = _parse_actions(body)
                if error:
                    self._json({"error": error}, 400)
                    return
                self._json(self.provider.update_scene(
                    str(scene_id), name=body.get("name"),
                    actions=specs or None, icon_url=body.get("icon_url")))
            elif path == "/api/scene/create":
                name = body.get("name")
                specs, error = _parse_actions(body)
                if not name:
                    self._json({"error": "name required"}, 400)
                    return
                if error or not specs:
                    self._json({"error": error or "did or actions required"}, 400)
                    return
                self._json(self.provider.create_scene(
                    name, actions=specs, icon_url=body.get("icon_url")))
            elif path == "/api/timer/create":
                hour = body.get("hour")
                minute = body.get("minute")
                specs, error = _parse_actions(body)
                if hour is None or minute is None:
                    self._json({"error": "hour/minute required"}, 400)
                    return
                if error or not specs:
                    self._json({"error": error or "did or actions required"}, 400)
                    return
                try:
                    hour, minute = int(hour), int(minute)
                except (TypeError, ValueError):
                    self._json({"error": "hour/minute must be integers"}, 400)
                    return
                repeat = body.get("repeat") or "daily"
                if repeat not in ("daily", "workday", "holiday", "weekly"):
                    self._json({"error": "unknown repeat"}, 400)
                    return
                days = body.get("days") or []
                if repeat == "weekly" and not days:
                    self._json({"error": "days required for weekly"}, 400)
                    return
                name = body.get("name") or ""
                self._json(self.provider.create_timer(
                    None, hour, minute, name=name, actions=specs,
                    repeat=repeat, days=days))
            elif path == "/api/timer/update":
                scene_id = body.get("scene_id") or body.get("id")
                if not scene_id:
                    self._json({"error": "scene_id required"}, 400)
                    return
                hour = body.get("hour")
                minute = body.get("minute")
                if hour is not None and minute is not None:
                    try:
                        hour, minute = int(hour), int(minute)
                    except (TypeError, ValueError):
                        self._json({"error": "hour/minute must be integers"}, 400)
                        return
                else:
                    hour = minute = None
                self._json(self.provider.update_timer(
                    str(scene_id), name=body.get("name") or None,
                    hour=hour, minute=minute))
            elif path == "/api/timer/delete":
                scene_id = body.get("scene_id") or body.get("id")
                if not scene_id:
                    self._json({"error": "scene_id required"}, 400)
                    return
                self._json(self.provider.delete_timer(str(scene_id)))
            elif path == "/api/scene/status":
                scene_id = body.get("scene_id") or body.get("id")
                if not scene_id:
                    self._json({"error": "scene_id required"}, 400)
                    return
                self._json(self.provider.set_scene_status(
                    str(scene_id), bool(body.get("enable"))))
            elif path == "/api/scene/run":
                scene_id = body.get("scene_id") or body.get("id")
                if not scene_id:
                    self._json({"error": "scene_id required"}, 400)
                    return
                self._json(self.provider.run_scene(str(scene_id)))
            elif path == "/api/refresh":
                did = body.get("did")
                refresh = getattr(self.provider, "refresh_state", None)
                if refresh is None:
                    self._json({"state": self.provider.view(did).get("state") or {}})
                    return
                self._json({"state": refresh(did)})
            else:
                self._json({"error": "not found"}, 404)
        except KeyError as exc:
            self._json({"error": "not found: %s" % exc}, 404)
        except Exception as exc:                   # noqa: BLE001
            log_line("POST %s failed: %s: %s" % (path, type(exc).__name__, exc))
            self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)


def _parse_query(path):
    if "?" not in path:
        return {}
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(path.split("?", 1)[1]).items()}


class AppServer(ThreadingHTTPServer):
    """ThreadingHTTPServer already mixes in ThreadingMixIn -- do not add it twice."""

    daemon_threads = True
    allow_reuse_address = True


def create_server(provider=None, host="127.0.0.1", port=0, credentials_path=None,
                  quiet=True):
    """Build a server bound to an ephemeral port unless one is given.

    ``provider=None`` means "decide from the credentials on disk, and keep
    following that file so a QR login takes effect without a restart".  Any
    explicit provider is used as-is (tests rely on this: they must never touch
    the network just because this machine happens to be logged in).
    """
    credentials_path = credentials_path or os.path.join(_data_dir(), "credentials.json")
    if provider is None:
        provider = make_provider(credentials_path)
        stamp = _credentials_stamp(credentials_path)
        follow = True
    else:
        stamp = None                       # explicit provider: never swap it
        follow = False
    handler = type("BoundHandler", (_Handler,), {
        "provider_state": {"provider": provider, "stamp": stamp, "follow": follow,
                           "lock": threading.Lock()},
        "credentials_path": credentials_path,
        "login_flow": LoginFlow(credentials_path),
        "quiet": quiet,
    })
    server = AppServer((host, port), handler)
    return server


def _app_version():
    try:
        from .. import VERSION
        return VERSION
    except Exception:                     # noqa: BLE001 - never break the UI over this
        return ""


def plugin_coverage():
    """Which cached plugins have a text table, for the account panel."""
    root = os.path.join(_data_dir(), "plugins")
    rows = []
    try:
        for name in sorted(os.listdir(root)):
            texts = os.path.join(root, name, "texts.json")
            info = os.path.join(root, name, "info.json")
            if not os.path.isfile(texts):
                continue
            count = 0
            try:
                with open(texts, encoding="utf-8") as handle:
                    count = len(json.load(handle) or {})
            except (OSError, ValueError):
                count = 0
            has_package = os.path.isfile(os.path.join(root, name, "package.zip"))
            rows.append({"model": name, "texts": count, "package": has_package,
                         "info": os.path.isfile(info)})
    except OSError:
        return []
    return rows


def _scene_result(payload, action):
    """Map a scene service reply to ``{ok, code, message}``.

    Success comes back as the boolean ``true`` (``NewRunScene``) or as
    ``{code: 0}`` depending on the call -- treating ``true`` as a dict once
    made every successful run look like a failure.
    """
    if payload is True:
        code = 0
    elif payload is False:
        code = -1
    elif isinstance(payload, dict):
        code = payload.get("code", 0)
    else:
        code = 0 if payload else -1
    ok = cloud.is_success(code)
    return {"ok": ok, "code": code,
            "message": "" if ok else ("%s\u5931\u8d25 (code %s)" % (action, code))}


def _parse_actions(body):
    """Normalise ``actions`` (or the single-device fields) into spec dicts.

    Returns ``(specs, error)``; every spec is ``{did, kind, siid, piid,
    aiid, value, inputs}`` with the numbers already converted.
    """
    raw = body.get("actions")
    if raw is None:
        raw = []
        if body.get("did"):
            raw = [{"did": body.get("did"),
                    "kind": body.get("kind") or "prop",
                    "siid": body.get("siid"), "piid": body.get("piid"),
                    "aiid": body.get("aiid"), "value": body.get("value"),
                    "inputs": body.get("inputs")}]
    if not isinstance(raw, list):
        return [], "actions must be a list"
    specs = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("did"):
            return [], "each action needs a did"
        kind = item.get("kind") or "prop"
        if kind not in ("prop", "action"):
            return [], "kind must be prop or action"
        try:
            siid = int(item["siid"])
            piid = int(item["piid"]) if item.get("piid") is not None else None
            aiid = int(item["aiid"]) if item.get("aiid") is not None else None
        except (KeyError, TypeError, ValueError):
            return [], "siid/piid/aiid must be integers"
        specs.append({"did": str(item["did"]), "kind": kind, "siid": siid,
                       "piid": piid, "aiid": aiid,
                       "value": item.get("value"),
                       "inputs": item.get("inputs")})
    return specs, ""


def _newest_seen(state_seen):
    """Newest timestamp in a ``{"2.1": stamp}`` map, or 0 when empty."""
    values = [value for value in (state_seen or {}).values()
              if isinstance(value, (int, float))]
    return int(max(values)) if values else 0


def _plugin_text_count(model_name):
    """How many labelled plugin strings are cached for this model."""
    if not model_name:
        return 0
    path = os.path.join(_data_dir(), "plugins", model_name.replace("/", "_"),
                        "texts.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return len(json.load(handle) or {})
    except (OSError, ValueError):
        return 0


def _credentials_stamp(path):
    """Cheap change detector for the credentials file.

    ``(mtime, size)`` rather than a bare mtime: a re-login can rewrite the file
    within the same clock tick on some filesystems, and the token length is a
    free second signal.  ``None`` means "no file", which is *not* the same as
    "do not follow the file" -- see ``_Handler.provider``.
    """
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime, info.st_size)


def sync_plugins_in_background(provider):
    """Fill in missing plugin text tables without blocking the UI.

    Every device model can ship a plugin whose ``texts.json`` gives official
    wording (enum labels, fault codes).  Cached packages are parsed locally;
    only genuinely missing ones hit the network, so a normal start is cheap.
    """
    if getattr(provider, "demo", True) or not hasattr(provider, "client"):
        return None
    from ..xiaomi import plugin as plugin_mod
    root = os.path.join(_data_dir(), "plugins")

    def run():
        try:
            models = plugin_mod.missing_models(provider.models(), root)
            if not models:
                return
            for row in plugin_mod.sync_all(provider.client, models, root):
                log_line("plugin sync %s strings=%s texts=%s %s" % (
                    row.get("model"), row.get("strings"), row.get("texts"),
                    row.get("error") or ""))
        except Exception as exc:            # noqa: BLE001 - must never kill the UI
            log_line("plugin sync failed: %s" % (exc,))

    thread = threading.Thread(target=run, name="plugin-sync", daemon=True)
    thread.start()
    return thread


def make_provider(credentials_path=None):
    """Cloud provider when credentials exist, demo otherwise.

    An unreadable/half-written credentials file falls back to demo with a log
    line instead of raising: ``provider`` runs on every request, and a corrupt
    JSON blob used to turn the whole UI into 500s.
    """
    credentials_path = credentials_path or os.path.join(_data_dir(), "credentials.json")
    try:
        credentials = auth.load_credentials(credentials_path)
    except (OSError, ValueError) as exc:
        log_line("credentials unreadable (%s): %s" % (type(exc).__name__, exc))
        return DemoProvider()
    if credentials is None:
        return DemoProvider()
    return CloudProvider(credentials)


def serve(port=0, open_browser=True, credentials_path=None, quiet=True):
    resolved = credentials_path or os.path.join(_data_dir(), "credentials.json")
    log_line("serve: data=%s credentials=%s exists=%s" % (
        _data_dir(), resolved, os.path.exists(resolved)))
    provider = make_provider(credentials_path)
    server = create_server(provider=None, port=port,
                           credentials_path=credentials_path, quiet=quiet)
    host, bound_port = server.server_address[0], server.server_address[1]
    url = "http://%s:%d/" % (host, bound_port)
    print("mihome-pc web \u5df2\u542f\u52a8: %s" % url)
    print("provider: %s" % ("cloud" if not provider.demo else "demo"))
    if not provider.demo:
        print("plugins: \u540e\u53f0\u8865\u5168\u6587\u6848\u8868")
        sync_plugins_in_background(provider)
    if open_browser:
        from .launcher import open_ui
        opened = open_ui(url)
        print("window: %s" % (opened or "please open the URL manually"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("bye")
    finally:
        server.server_close()
    return 0


def _now():
    return time.time()
