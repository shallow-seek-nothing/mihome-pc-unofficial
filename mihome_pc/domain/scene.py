"""Turn the cloud scene payload into what the UI renders.

``GetSimpleSceneList`` returns four lists (manual / auto / voice / distance) of
records with nested ``scene_action.actions[].payload_json``.  The UI only needs
a flat card per manual scene plus a count of the rest, so all of that shape
knowledge lives here and stays unit-testable without a network.
"""
from . import timer as timer_mod

__all__ = ["normalise", "category_label", "CATEGORY_LABELS", "TRIGGER_LABELS"]

# Scene ``tags.classification`` values seen in this account; unknown values fall
# through verbatim so nothing is hidden.
CATEGORY_LABELS = {
    "lighting": "\u706f\u5149",
    "curtain": "\u7a97\u5e18",
    "airconditioner": "\u7a7a\u8c03",
    "ac": "\u7a7a\u8c03",
    "security": "\u5b89\u9632",
    "environment": "\u73af\u5883",
    "water_heater": "\u70ed\u6c34\u5668",
    "socket": "\u63d2\u5ea7",
    "switch": "\u5f00\u5173",
}


def category_label(classification):
    """Chinese label for a scene classification (raw value when unknown)."""
    key = (classification or "").strip().lower()
    return CATEGORY_LABELS.get(key, classification or "")


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _action(record):
    """One action, plus the fields needed to re-submit it (edit mode)."""
    payload = record.get("payload_json") or {}
    command = payload.get("command") or ""
    value = payload.get("value")
    siid = piid = aiid = None
    inputs = None
    if command == "action" and isinstance(value, dict):
        siid = value.get("siid")
        aiid = value.get("aiid")
        inputs = value.get("in") or []
    elif isinstance(value, list) and value:
        first = value[0] if isinstance(value[0], dict) else {}
        siid = first.get("siid")
        piid = first.get("piid")
        value = first.get("value")
    return {
        "device": payload.get("device_name") or "",
        "did": payload.get("did") or "",
        "model": payload.get("model") or "",
        "command": command,
        "delay": _int(payload.get("delay_time")),
        "kind": "action" if command == "action" else "prop",
        "siid": siid,
        "piid": piid,
        "aiid": aiid,
        "value": value,
        "inputs": inputs,
    }


def _unique(items):
    seen = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _scene(record):
    actions = ((record.get("scene_action") or {}).get("actions") or [])
    parsed = [_action(action) for action in actions if isinstance(action, dict)]
    tags = record.get("tags") or {}
    classification = (tags.get("classification") or "").strip()
    return {
        "id": str(record.get("scene_id") or ""),
        "name": record.get("name") or "\u672a\u547d\u540d\u573a\u666f",
        "enabled": bool(record.get("enable", True)),
        "category": classification,
        "category_label": category_label(classification),
        "icon": record.get("icon_url") or "",
        "updated": _int(record.get("last_edit_time")),
        "actions": parsed,
        "devices": _unique([action["device"] for action in parsed]),
    }


TRIGGER_LABELS = {
    "timer": "\u5b9a\u65f6",
    "sensor": "\u4f20\u611f\u5668",
    "device": "\u8bbe\u5907\u72b6\u6001",
    "geofence": "\u5730\u7406\u56f4\u680f",
    "manual": "\u624b\u52a8",
    "sunset": "\u65e5\u843d",
    "sunrise": "\u65e5\u51fa",
}


def _clock_from_cron(cron):
    """``"08:45"`` from ``0 45 8 * * * *`` (7-field cron), else ``""``."""
    parts = str(cron or "").split()   # value_json can arrive as an int
    if len(parts) < 3:
        return ""
    try:
        minute, hour = int(parts[1]), int(parts[2])
    except ValueError:
        return ""
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return "%02d:%02d" % (hour, minute)
    return ""


def _trigger(trigger):
    """``{src, label, detail, cron, clock, timezone}`` for one trigger record."""
    src = (trigger.get("src") or "").strip()
    extra = trigger.get("extra_json") or {}
    cron = trigger.get("value_json") or ""
    filter_kind = (extra.get("filter") or "") if isinstance(extra, dict) else ""
    # 没有节假日日历就算不准：工作日只能按周一至周五估算，
    # 节假日由云端按法定日历筛选，本地无法判定，宁可不给日期。
    if filter_kind == "cn_workday":
        status = "approximate_weekday"
    elif filter_kind == "cn_freeday":
        status = "unavailable_holiday"
    else:
        status = "exact"
    upcoming = None
    if filter_kind != "cn_freeday":
        upcoming = timer_mod.next_run(cron,
                                      weekdays_only=filter_kind == "cn_workday")
    return {
        "src": src,
        "label": TRIGGER_LABELS.get(src, src),
        # name carries the human sentence the app shows, e.g.
        # "08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5(\u4e2d\u56fd\u5927\u9646)"
        "detail": trigger.get("name") or "",
        "cron": cron,
        "clock": _clock_from_cron(cron),
        "next_run": upcoming.strftime("%Y-%m-%dT%H:%M:%S") if upcoming else "",
        "next_run_status": status,
        "next_run_approx": status != "exact",
        "timezone": (extra.get("time_zone") or "") if isinstance(extra, dict) else "",
    }


def _automation(record, detail=None):
    """One automation row; ``detail`` is a ``GetSceneInfo`` reply when we have it."""
    source = detail if isinstance(detail, dict) else record
    triggers = ((source.get("scene_trigger") or {}).get("triggers") or [])
    first = _trigger(triggers[0]) if triggers and isinstance(triggers[0], dict) else {}
    if not first:
        hint = record.get("first_trigger") or {}
        first = {"src": (hint.get("src") or ""), "label": "", "detail": "",
                 "cron": "", "timezone": ""}
        first["label"] = TRIGGER_LABELS.get(first["src"], first["src"])
    actions = ((source.get("scene_action") or {}).get("actions") or [])
    names = _unique([(action.get("payload_json") or {}).get("device_name")
                     for action in actions if isinstance(action, dict)])
    return {
        "id": str(record.get("scene_id") or record.get("id") or ""),
        "name": record.get("name") or "\u672a\u547d\u540d\u81ea\u52a8\u5316",
        "enabled": bool(source.get("enable", record.get("enable", True))),
        "trigger": first,
        "devices": names,
        "action_count": len(actions),
    }


def normalise(payload, home=None, details=None):
    """``{"home": {...}, "scenes": [...], "automations": [...]}``.

    Scenes keep their order (the app shows them in the order the user arranged)
    and every field the UI reads is present, even when the cloud omits it.
    ``details`` maps scene id -> ``GetSceneInfo`` reply, used to show a timer's
    real cadence (the simple list only says ``src == timer``).
    """
    data = payload if isinstance(payload, dict) else {}
    details = details or {}
    scenes = [_scene(record) for record in data.get("manual_scene_info_list") or []
              if isinstance(record, dict) and record.get("scene_id")]
    automations = [_automation(record, details.get(str(record.get("scene_id"))))
                   for record in data.get("auto_scene_info_list") or []
                   if isinstance(record, dict)]
    voice = data.get("voice_scene_info_list") or []
    return {
        "home": home or {},
        "scenes": scenes,
        "automations": automations,
        "voice_count": len(voice) if isinstance(voice, list) else 0,
    }
