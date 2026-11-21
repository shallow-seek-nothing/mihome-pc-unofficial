"""Turn a parsed spec (plus catalog/card metadata) into UI controls.

This is the "spec driven generation" half of the v3 redesign: instead of
hand-writing a control list per model (what v2 did for 14 devices), we derive
one from the spec's ``format`` / ``value-list`` / ``value-range`` / ``unit``
and only let the APK metadata override presentation details.
"""
import json
import os

from ..xiaomi import spec as spec_mod
from . import labels, plugin_text

__all__ = [
    "Control",
    "DeviceView",
    "build_controls",
    "build_actions",
    "build_remote",
    "ActionControl",
    "select_primary",
    "load_catalog_entry",
    "load_card_layout",
]

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "assets")

# Service names whose properties are diagnostics, not controls.
_DIAGNOSTIC_SERVICES = frozenset([
    "device-information", "indicator-light", "environment", "alarm",
    "telnet-control", "wifi-connection", "custom", "statistics",
])

# Protocol plumbing that must never reach the UI.  ``message-router`` (siid 5 on
# TVs) is a ``post(request) -> response`` RPC the firmware uses internally; the
# generic renderer turned it into a 「消息传递 / 执行」 button that sent an empty
# string (observed 2026-11-21 on xiaomi.tv.fsv1).
_PROTOCOL_SERVICES = frozenset(["message-router"])

# Widget preference: enum -> list of options, numbers -> slider when a range
# with a step exists, otherwise a stepper.
_RANGE_SLIDER_MIN_STEPS = 2

# The spec ships English unit names; the UI is Chinese.  Unknown units fall back
# to the raw string so no information is ever hidden.
UNIT_LABELS = {
    "percentage": "%",
    "minutes": "\u5206\u949f",
    "minute": "\u5206\u949f",
    "hours": "\u5c0f\u65f6",
    "hour": "\u5c0f\u65f6",
    "seconds": "\u79d2",
    "second": "\u79d2",
    "days": "\u5929",
    "day": "\u5929",
    "celsius": "\u2103",
    "kelvin": "K",
    "fahrenheit": "\u2109",
    "arcdegrees": "\u00b0",
    "rgb": "",
    "none": "",
}


def unit_label(unit, value_range=None):
    """English spec unit -> display label.

    A few specs declare ``unit: percentage`` while exposing the raw 0..65535
    scale (Yeelight brightness), which made the UI read "25000 %".  In that case
    the unit is dropped rather than lying about the number.
    """
    if not unit:
        return ""
    label = UNIT_LABELS.get(str(unit).lower(), str(unit))
    if label == "%" and value_range and value_range[1] > 1000:
        return ""
    return label


class Control:
    """One interactive element on a device page."""

    __slots__ = ("siid", "piid", "name", "label", "service_name", "service_label",
                 "widget", "options", "range", "unit", "writable", "readable",
                 "notifiable", "format", "unit_label", "command_value",
                 "scale", "time_of_day", "device_type")

    def __init__(self, prop, service, device_type="", texts=None):
        self.siid = prop.siid
        self.piid = prop.piid
        self.name = prop.name
        raw_label = " ".join((prop.label or "").split())
        if not labels.is_cjk(raw_label):
            # the i18n table had no Chinese for this one; fall back to our map
            raw_label = labels.zh_name(prop.name, raw_label)
        self.label = raw_label or prop.name
        self.service_name = service.name
        service_raw = " ".join((service.label or "").split())
        if not labels.is_cjk(service_raw):
            service_raw = labels.zh_name(service.name, service_raw, service=True)
        self.service_label = service_raw or service.name
        self.format = prop.format
        self.widget = _widget_for(prop)
        self.options = []
        for option in prop.value_list or ():
            label = option.label
            # 插件里的官方说法优先（spec 说 Drum Clean，我们自己的词典写「桶自洁」，
            # 手机上是「筒自洁」）——取不到才回落到 spec i18n / 内置词典。
            from_plugin = plugin_text.value_label(texts, prop.name, label, device_type)
            if from_plugin:
                label = from_plugin
            elif not labels.is_cjk(label):
                label = labels.zh_value(label, device_type)
            self.options.append((option.value, label))
        self.range = prop.value_range
        self.unit = prop.unit
        self.unit_label = unit_label(prop.unit, prop.value_range)
        self.writable = prop.writable
        self.readable = prop.readable
        self.notifiable = prop.notifiable
        self.command_value = _command_value(prop) if self.widget == "command" else None
        self.device_type = device_type
        self.scale = _display_scale(prop)
        self.time_of_day = _is_time_of_day(prop)

    @property
    def key(self):
        return (self.siid, self.piid)

    @property
    def address(self):
        """``"2.1"`` -- the form users see and the API takes."""
        return "%d.%d" % (self.siid, self.piid)

    def to_dict(self):
        return {
            "siid": self.siid, "piid": self.piid, "address": self.address,
            "name": self.name, "label": self.label,
            "service": self.service_name, "service_label": self.service_label,
            "widget": self.widget, "format": self.format,
            "options": [[v, lab] for v, lab in self.options],
            "range": list(self.range) if self.range else None,
            "unit": self.unit,
            "unit_label": self.unit_label,
            "command_value": self.command_value,
            "scale": self.scale,
            "time_of_day": self.time_of_day,
            "writable": self.writable, "readable": self.readable,
            "notifiable": self.notifiable,
        }

    def __repr__(self):
        return "<Control %s %s %s>" % (self.address, self.name, self.widget)


# spec action identifier -> position on the remote panel.
# Evidence: television:0000A010 exposes service 7 "remote-control" with exactly
# these action types (press-up/down/left/right/ok, press-home/back/menu,
# press-volume-up/down, turn-off) plus play/pause and turn-on elsewhere.
REMOTE_ROLES = {
    "press-up": "up",
    "press-down": "down",
    "press-left": "left",
    "press-right": "right",
    "press-ok": "ok",
    "press-home": "home",
    "press-back": "back",
    "press-menu": "menu",
    "press-settings": "settings",
    "press-volume-up": "volume_up",
    "press-volume-down": "volume_down",
    "turn-off": "power",
    "turn-on": "power_on",
    "play": "play",
    "pause": "pause",
}

# Properties the remote panel owns (volume / mute / input source) are collected
# by build_remote itself and exposed as DeviceView.remote_keys.  They are
# deliberately *not* dropped from DeviceView.controls: the panel needs their
# control objects to read and write current values, and dropping them there
# left a TV with zero controls -- hence zero polled properties, hence an empty
# state in the UI (fixed 2026-11-21, see docs/03).


class ActionControl:
    """A spec *action* (an RPC with optional inputs), e.g. start/cancel cooking.

    Real evidence (xiaomi.health_pot.p1v2, 2026-11-19): the device exposes
    ``start-cook`` with ``in: [3]``, i.e. "pass the value of property iid 3"
    (its mode).  Sending ``in: []`` is refused with ``-704220025`` and a bad
    value with ``-704220043``, while ``cancel-cooking`` (``in: []``) answers 0.
    So an action's payload is the *current value of the referenced properties*.
    """

    __slots__ = ("siid", "aiid", "name", "label", "service_label",
                 "in_refs", "params")

    def __init__(self, action, service):
        self.siid = service.siid
        self.aiid = action.aiid
        self.name = action.name
        raw = " ".join((action.label or action.name or "").split())
        if not labels.is_cjk(raw):
            raw = labels.zh_action(action.name, raw)
        self.label = raw
        self.service_label = " ".join((service.label or service.name or "").split())
        # 参数有两种：裸 iid（引用另一个属性的当前值）和 inline 字面量（带
        # format/range/enum）。以前只保留了 piid，执行层于是把 inline 参数也当成
        # 属性引用去读当前值，发出去的就是 None。这里保留完整、有序的描述。
        self.params = tuple(
            {"piid": arg.piid, "inline": bool(arg.inline),
             "label": arg.label or arg.name or "",
             "format": arg.format or "",
             "value_range": list(arg.value_range) if arg.value_range else None,
             "value_list": [[o.value, o.name, o.label] for o in arg.value_list]
             if arg.value_list else None,
             "unit": arg.unit}
            for arg in action.in_args)
        # 兼容字段：只放真正的属性引用
        self.in_refs = tuple(arg.piid for arg in action.in_args if not arg.inline)

    @property
    def address(self):
        return "%d.%d" % (self.siid, self.aiid)

    def to_dict(self):
        return {"siid": self.siid, "aiid": self.aiid, "address": self.address,
                "name": self.name, "label": self.label,
                "service_label": self.service_label,
                "in_refs": list(self.in_refs),
                "params": [dict(param) for param in self.params]}

    def __repr__(self):
        return "<ActionControl %s %s>" % (self.address, self.name)


def build_actions(spec):
    """All callable actions of a device, diagnostics excluded."""
    actions = []
    for service in spec.services:
        if service.name in _DIAGNOSTIC_SERVICES or service.name in _PROTOCOL_SERVICES:
            continue
        for action in service.actions:
            actions.append(ActionControl(action, service))
    return actions


def _display_scale(prop):
    """How the *displayed* number differs from the wire value.

    A Yeelight bulb reports brightness on the raw 1..65535 scale, which shows up
    as "25000" in a UI where the Mi Home app says 38%.  We keep the wire value
    for reads/writes and only convert for display.
    """
    if prop.name == "brightness" and prop.value_range and prop.value_range[1] > 100:
        return {"type": "percent", "max": prop.value_range[1],
                "min": prop.value_range[0]}
    return None


def build_remote(spec):
    """Assemble a remote-control panel, or ``None`` when the device has none."""
    roles = {}
    for service in spec.services:
        for action in service.actions:
            role = REMOTE_ROLES.get(action.name)
            if not role:
                continue
            if role in roles and service.name == "television":
                continue          # prefer the remote-control service's variant
            raw = " ".join((action.label or action.name or "").split())
            if not labels.is_cjk(raw):
                raw = labels.zh_action(action.name, raw)
            roles[role] = {"siid": service.siid, "aiid": action.aiid,
                           "label": raw,
                           # Real finding (xiaomi.tv.fsv1): ``turn-on`` sits in
                           # service 6 ``ble-control``, i.e. waking the TV goes
                           # over Bluetooth LE from the phone.  The cloud has no
                           # path to it, which is why a powered-off TV cannot be
                           # woken from Mi Home (only 小爱 can).
                           "needs_ble": service.name == "ble-control"}
    if not roles:
        return None
    # Guard: a washing machine exposes ``pause`` too, and building a "remote"
    # around that single key produced an empty panel with one stray button.
    # A real remote has a remote-control service or at least two direction keys.
    has_remote_service = any(service.name == "remote-control"
                             for service in spec.services)
    directions = sum(1 for role in ("up", "down", "left", "right", "ok")
                     if role in roles)
    if not has_remote_service and directions < 2:
        return None
    properties = {}
    for name in ("volume", "mute", "input-source", "input-control"):
        for service in spec.services:
            prop = service.property(name)
            if prop is not None:
                key = "input-source" if name == "input-control" else name
                properties[key] = {"siid": prop.siid, "piid": prop.piid,
                                   "writable": prop.writable,
                                   "options": [[o.value, o.label]
                                               for o in prop.value_list or []]}
                break
    return {"roles": roles, "properties": properties}


def _remote_property_keys(remote):
    """(siid, piid) pairs the remote panel displays."""
    keys = set()
    for ref in (remote or {}).get("properties", {}).values():
        keys.add((ref["siid"], ref["piid"]))
    return keys


def _command_value(prop):
    """Payload for a write-only command property.

    A real Yeelight bulb exposes ``toggle`` / ``bright-scene`` / ``bright-adjust``
    as write-only integers spanning the whole int32 range -- rendering those as a
    stepper (the old behaviour) both looked broken and would have sent a
    meaningless -2147483648.  Such properties are executed as a one-shot command
    instead; ``1`` and the first enum option are the only payloads observed to
    work against real hardware.
    """
    if prop.value_list:
        return prop.value_list[0].value
    return 1


def _is_time_of_day(prop):
    """True for "seconds since midnight" style properties.

    The pet water dispenser's "do not disturb window" reports ``79200`` with
    unit ``seconds``, which the UI showed literally as "79200 秒" instead of
    22:00.
    """
    if (prop.unit or "").lower() not in ("seconds", "second"):
        return False
    if not prop.value_range:
        return False
    low, high = prop.value_range[0], prop.value_range[1]
    return low >= 0 and 60 <= high <= 86400


def _is_noise(prop):
    """Read-only values with an absurd range (raw int32 debug fields).

    e.g. "log time zone" reports -2147483647; printing that helps nobody.
    """
    if prop.writable or not prop.value_range:
        return False
    span = prop.value_range[1] - prop.value_range[0]
    return span >= 1000000


def _widget_for(prop):
    """Decide how the renderer should draw this property."""
    if not prop.readable:
        # write-only: the value can never be displayed, so it is a command
        return "command"
    if not prop.writable:
        # telemetry: a switch-looking control that cannot be toggled is a lie,
        # and a slider for "remaining time" reads as if it were adjustable
        if prop.format == "bool":
            return "state"
        if prop.value_range:
            return "progress"
        # A read-only enum is a *state*, not a picker: showing the options as a
        # row of chips invites clicks that can never do anything.
        return "value"
    if prop.value_list:
        return "select" if len(prop.value_list) > 4 else "buttons"
    if prop.format == "bool":
        return "switch"
    if prop.value_range:
        low, high, step = prop.value_range
        steps = (high - low) / float(step) if step else 0
        return "slider" if steps >= _RANGE_SLIDER_MIN_STEPS else "stepper"
    base = spec_mod.WIDGET_BY_FORMAT.get(prop.format, "text")
    return "stepper" if base == "number" else base


def build_controls(spec, include_diagnostics=False, texts=None):
    """All controls for a device, ordered by service then iid.

    ``texts`` is the model's plugin wording (see ``domain/plugin_text``); it
    only affects enum labels, and everything falls back to the spec when the
    plugin has no matching key.
    """
    controls = []
    for service in spec.services:
        if service.name in _PROTOCOL_SERVICES:
            continue
        if not include_diagnostics and service.name in _DIAGNOSTIC_SERVICES:
            # keep the properties readable, just do not surface them as controls
            continue
        for prop in service.properties:
            if not (prop.readable or prop.writable or prop.notifiable):
                continue
            if _is_noise(prop):
                continue
            controls.append(Control(prop, service, spec.device_type, texts))
    return controls


def select_primary(spec, controls=None):
    """Pick the control that drives the device card.

    Order of preference: the writable ``on`` bool, then any writable bool, then
    the first writable control of any kind.
    """
    controls = controls if controls is not None else build_controls(spec)
    candidates = [c for c in controls if c.writable]
    for control in candidates:
        if control.name == "on" and control.format == "bool":
            return control
    for control in candidates:
        if control.format == "bool":
            return control
    return candidates[0] if candidates else None


class DeviceView:
    """A device as the UI needs to see it."""

    __slots__ = ("did", "model", "name", "online", "spec", "catalog", "layout",
                 "controls", "actions", "remote", "remote_keys", "primary",
                 "texts")

    def __init__(self, did, model, name, online, spec, catalog=None, layout=None,
                 texts=None):
        self.did = did
        self.model = model
        self.name = name
        self.online = online
        self.spec = spec
        self.catalog = catalog or {}
        self.layout = layout or {}
        self.texts = texts or {}
        self.remote = build_remote(spec) if spec else None
        self.remote_keys = _remote_property_keys(self.remote)
        self.controls = build_controls(spec, texts=self.texts) if spec else []
        self.actions = build_actions(spec) if spec else []
        if self.remote:
            # the panel owns these keys: repeating them as generic rows is
            # confusing.  Only *actions* are removed here -- properties stay in
            # controls so they can still be polled and driven from the panel.
            self.actions = [a for a in self.actions if a.name not in REMOTE_ROLES]
        self.primary = select_primary(
            spec, [c for c in self.controls if c.key not in self.remote_keys]) \
            if spec else None

    # -- display -------------------------------------------------------
    @property
    def display_name(self):
        return self.name or self.catalog.get("n") or self.model

    @property
    def category(self):
        return self.catalog.get("c") or (self.spec.device_type if self.spec else "")

    @property
    def icon(self):
        return self.catalog.get("ic") or ""

    @property
    def card_layout(self):
        return self.layout.get("l")

    # -- polling -------------------------------------------------------
    def read_items(self):
        """``[(did, siid, piid), ...]`` for a property poll."""
        if not self.spec:
            return []
        return [(self.did, p.siid, p.piid) for p in self.spec.readable_properties()]

    @property
    def status_controls(self):
        """Read-only telemetry, gathered so the UI can show it as one block.

        ``device-information`` is excluded: firmware/model/serial are static
        facts, not device status, and would bury the interesting rows.
        """
        return [control for control in self.controls
                if control.key not in self.remote_keys
                and not control.writable
                and control.service_label not in ("\u8bbe\u5907\u4fe1\u606f",)]

    def poll_items(self):
        """Everything the UI displays, cheaper than the full readable set.

        This deliberately includes the properties the remote panel owns
        (volume / mute / input source): they are shown as live values, so they
        must be polled even though they are not generic rows.
        """
        return [(self.did, c.siid, c.piid) for c in self.controls if c.readable]

    def groups(self):
        """Per service: the controls we can *set* and the ones we can only *read*.

        Keeping them apart is a product requirement: a washing machine's program
        picker and its remaining-time readout look identical as rows, and mixing
        them made the UI feel like it accepted edits it did not.
        """
        buckets = []
        index = {}
        for control in self.controls:
            if control.key in self.remote_keys:
                # shown on the remote panel instead, never twice
                continue
            key = control.service_label
            if key not in index:
                index[key] = len(buckets)
                buckets.append({"label": key, "writable": [], "readonly": []})
            buckets[index[key]]["writable" if control.writable else "readonly"].append(control)
        return buckets

    def control(self, siid, piid):
        for candidate in self.controls:
            if candidate.siid == siid and candidate.piid == piid:
                return candidate
        return None

    def to_dict(self):
        return {
            "did": self.did, "model": self.model,
            "name": self.display_name, "category": self.category,
            "online": self.online, "icon": self.icon,
            "card_layout": self.card_layout,
            "urn": self.spec.urn if self.spec else "",
            "primary": self.primary.to_dict() if self.primary else None,
            "controls": [c.to_dict() for c in self.controls],
            "actions": [a.to_dict() for a in self.actions],
            "remote": self.remote,
            "fault_notes": plugin_text.fault_notes(self.texts),
            "status_list": [c.to_dict() for c in self.status_controls],
            "groups": [{"label": group["label"],
                        "writable": [c.to_dict() for c in group["writable"]],
                        "readonly": [c.to_dict() for c in group["readonly"]]}
                       for group in self.groups()],
        }

    def __repr__(self):
        return "<DeviceView %s %s %d controls>" % (self.did, self.model, len(self.controls))


# --------------------------------------------------------------------------
# metadata loaders (cached; the tables are a few MB)
# --------------------------------------------------------------------------

_CATALOG = None
_LAYOUTS = None


def _catalog_dirs():
    """代码目录优先，然后是用户数据目录。

    型号目录与卡片布局是从安卓包里提取的第三方数据，**不随仓库和安装包发布**，
    因此默认下这些文件并不存在；用户可以用自己拥有的安装包在本地生成，
    写到数据目录的 `catalog/` 下。升级安装不会动数据目录，所以生成过的目录能保留。
    """
    dirs = []
    try:
        from ..cli import data_dir          # 延迟导入，避免循环依赖
        dirs.append(os.path.join(data_dir(), "catalog"))
    except Exception:                       # noqa: BLE001 - 命令行不在时也要能跑
        pass
    dirs.append(ASSETS_DIR)
    return dirs


def _load(name):
    for directory in _catalog_dirs():
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):       # noqa: BLE001 - 坏文件不能弄挂界面
            continue
    return {}


def display_model(model):
    """给用户看的型号名。

    数据库里的 `model` 通常很好读（如 `minij.washer.v5`），但 spec 里写的是完整 URN；
    URN 对用户没有意义，只取其中的设备类型段（如 `washer`）。
    """
    text = (model or "").strip()
    if not text:
        return ""
    if not text.startswith("urn:"):
        return text
    parts = [part for part in text.split(":") if part]
    # urn:miot-spec-v2:device:<type>:<id>:<vendor>:<version>
    if len(parts) >= 4 and parts[2] == "device":
        return parts[3].replace("_", " ")
    return parts[-2] if len(parts) >= 2 else text


def load_catalog_entry(model):
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _load("catalog.json")
    return _CATALOG.get(model) or {}


def load_card_layout(model):
    global _LAYOUTS
    if _LAYOUTS is None:
        _LAYOUTS = _load("card_layouts.json")
    return _LAYOUTS.get(model) or {}
