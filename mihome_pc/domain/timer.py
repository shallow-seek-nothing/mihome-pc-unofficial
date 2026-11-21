"""Timer automations: the cron string, its label, and the payload to save.

A Mi Home \u5b9a\u65f6 is an automation whose trigger is ``timer.crontab``; the
cadence lives in ``scene_trigger.triggers[0]`` as a 7-field cron
(``0 45 8 * * * *`` = 08:45).  Editing it means re-posting the whole scene
record through ``AppSceneService/Edit`` with a few bookkeeping fields added --
all of that assembly is here so it can be unit-tested without a network.
"""
import copy
import re

__all__ = ["cron_for", "clock_from_cron", "describe", "build_payload",
           "filter_for", "label_for", "next_run", "retime", "WEEKDAYS"]

import datetime

# Cron day-of-week: 0 = Sunday .. 6 = Saturday (POSIX).  Evidence: the app's
# bitmask shortcuts are 62 = Mon..Fri and 65 = Sun+Sat
# (CorntabUtils$CorntabParam.java:88-93).
WEEKDAYS = ("\u5468\u65e5", "\u5468\u4e00", "\u5468\u4e8c", "\u5468\u4e09",
            "\u5468\u56db", "\u5468\u4e94", "\u5468\u516d")

# ``extra_json.filter`` values the app sends (TimeWindowSetting.java:259).
FILTERS = {"workday": "cn_workday", "holiday": "cn_freeday"}

_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})")


def cron_for(hour, minute, days=None):
    """7-field cron the scene trigger uses (second included).

    ``days`` is an iterable of 0..6 with 0 = Sunday; empty/None means every day.
    """
    hour, minute = int(hour), int(minute)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time out of range: %02d:%02d" % (hour, minute))
    dow = "*"
    if days:
        clean = sorted({int(day) for day in days})
        if any(day < 0 or day > 6 for day in clean):
            raise ValueError("day of week out of range: %r" % (clean,))
        dow = ",".join(str(day) for day in clean)
    return "0 %d %d * * %s *" % (minute, hour, dow)


def filter_for(kind):
    """``cn_workday`` / ``cn_freeday`` / ``""`` for the trigger's filter."""
    return FILTERS.get((kind or "").strip().lower(), "")


def label_for(hour, minute, days=None, kind=""):
    """Human label kept in ``trigger.name`` (what the phone prints)."""
    clock = "%02d:%02d" % (int(hour), int(minute))
    kind = (kind or "").strip().lower()
    if kind == "workday":
        return clock + " \u6cd5\u5b9a\u5de5\u4f5c\u65e5"
    if kind == "holiday":
        return clock + " \u6cd5\u5b9a\u8282\u5047\u65e5"
    if days:
        names = "\u3001".join(WEEKDAYS[int(day)] for day in sorted({int(d) for d in days}))
        return clock + " " + names
    return clock + " \u6bcf\u5929"


def retime(cron, hour, minute):
    """Replace only the clock in a cron string, keeping days and filters.

    Rewriting the whole cron (the first implementation) silently turned a
    "every Monday" timer into a daily one.
    """
    parts = str(cron or "").split()
    fresh = cron_for(hour, minute)
    if len(parts) != 7:
        return fresh
    new_parts = fresh.split()
    new_parts[3:] = parts[3:]
    return " ".join(new_parts)


def clock_from_cron(cron):
    """``(hour, minute)`` from a cron string, or ``None`` when unparsable."""
    parts = str(cron or "").split()   # value_json can arrive as an int
    if len(parts) < 3:
        return None
    try:
        minute, hour = int(parts[1]), int(parts[2])
    except (TypeError, ValueError):
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _parse_days(field):
    """``"*"`` -> None, ``"1,3"`` -> {1, 3}."""
    text = str(field or "").strip()
    if not text or text == "*":
        return None
    days = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            days.add(int(part))
        except ValueError:
            return None
    return days or None


def next_run(cron, after=None, weekdays_only=False):
    """Next ``datetime`` this cron fires, or ``None`` if unparseable.

    Only what the app actually writes is supported: fixed second/minute/hour,
    ``*`` for day-of-month and month, and either ``*`` or a day-of-week list
    for the weekday field.  ``weekdays_only`` replicates the 「法定工作日」
    filter **without** holiday data (Sat/Sun are skipped, holidays are not),
    which is why callers must say so in the UI.
    """
    parts = str(cron or "").split()
    if len(parts) != 7:
        return None
    try:
        minute, hour = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    days = _parse_days(parts[5])
    now = after or datetime.datetime.now()
    start = now.replace(second=0, microsecond=0)
    for offset in range(0, 8):
        day = (start + datetime.timedelta(days=offset)).date()
        if days is not None and (day.weekday() + 1) % 7 not in days:
            # cron weekday: 0 = Sunday, python weekday(): 0 = Monday
            continue
        if weekdays_only and day.weekday() >= 5:
            continue
        when = datetime.datetime.combine(day, datetime.time(hour, minute))
        if when > start:
            return when
    return None


def describe(trigger):
    """Human sentence for a timer trigger: ``08:45`` + whatever the app adds."""
    trigger = trigger or {}
    label = trigger.get("detail") or ""
    clock = clock_from_cron(trigger.get("cron"))
    if clock:
        text = "%02d:%02d" % clock
        rest = _CLOCK.sub("", label).strip()
        return (text + " " + rest).strip() if rest else text
    return label


def _relabel(name, hour, minute):
    """Keep the app's sentence (\u6cd5\u5b9a\u5de5\u4f5c\u65e5...) but move the clock.

    The trigger name is what the phone prints, so overwriting it with a bare
    time would lose the "workdays only" part; only the leading HH:MM is
    replaced when present.
    """
    clock = "%02d:%02d" % (int(hour), int(minute))
    text = (name or "").strip()
    if _CLOCK.match(text):
        return _CLOCK.sub(clock, text, count=1)
    if not text:
        return clock
    return clock + " " + text


def build_payload(info, name=None, hour=None, minute=None, home_id="",
                  owner_uid="", scene_id=None, label=None):
    """Scene record ready for ``Edit``, with the clock moved.

    ``info`` is a ``GetSceneInfo`` reply.  ``scene_id`` keeps the record's id
    (update in place); pass ``scene_id=""`` to create a copy instead.
    """
    if not isinstance(info, dict) or not info:
        raise ValueError("scene info is empty")
    payload = copy.deepcopy(info)
    for read_only in ("uid", "create_time", "last_edit_time", "icon_url"):
        payload.pop(read_only, None)
    triggers = ((payload.get("scene_trigger") or {}).get("triggers") or [])
    if not triggers:
        raise ValueError("scene has no trigger to retime")
    if hour is not None or minute is not None:
        current = clock_from_cron(triggers[0].get("value_json")) or (8, 0)
        hour = current[0] if hour is None else hour
        minute = current[1] if minute is None else minute
        triggers[0]["value_json"] = retime(triggers[0].get("value_json"),
                                            hour, minute)
        triggers[0]["name"] = label or _relabel(triggers[0].get("name"),
                                                 hour, minute)
    if name:
        payload["name"] = name
    if not payload.get("name"):
        raise ValueError("scene name is empty")
    if scene_id is not None:
        if scene_id:
            payload["scene_id"] = str(scene_id)
        else:
            payload.pop("scene_id", None)
    payload["edit_from"] = 1
    payload["value_format"] = 1
    payload["app_version"] = 19
    if owner_uid:
        payload["owner_uid"] = str(owner_uid)
    if home_id:
        payload["home_id"] = str(home_id)
    return payload
