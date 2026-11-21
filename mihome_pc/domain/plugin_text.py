"""Official UI wording shipped inside a device plugin.

A plugin (``data/plugins/<model>/texts.json``) carries ~360 resource strings
that are more specific than the MIoT spec i18n: the spec says ``Drum Clean``
and our dictionary says 桶自洁, while the plugin -- i.e. what the phone shows --
says 筒自洁.  So when a model has a plugin cached, its wording wins.

Matching is done on the value's English *name* (``Drum Clean`` ->
``mode_DrumClean``) and never on fuzzy text similarity: a wrong label is worse
than falling back to the spec translation.
"""
import json
import os
import re

__all__ = ["load", "value_label", "fault_notes"]

_SPLIT = re.compile("[^a-z0-9]+")

# The spec and the plugin do not agree on spelling for a few words.
_ALIASES = {
    "temp": "temperature",
    "temps": "temperature",
    "dry": "drying",
    "rinse": "rinsh",
    "times": "time",
    "minute": "minutes",
}


def _tokens(text):
    parts = [part for part in _SPLIT.split((text or "").lower()) if part]
    return {_ALIASES.get(part, part) for part in parts}


def _normalise(text):
    return "".join(_SPLIT.split((text or "").lower()))


def load(model, root_dir):
    """``{resource name: text}`` for a cached plugin, ``{}`` when there is none."""
    if not model or not root_dir:
        return {}
    path = os.path.join(root_dir, model.replace("/", "_"), "texts.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _tails(key):
    """``a_b_c`` -> ``["a_b_c", "b_c", "c"]`` (candidate value names)."""
    parts = key.split("_")
    return ["_".join(parts[index:]) for index in range(len(parts))]


def value_label(texts, prop_name, label, device_type=""):
    """Plugin wording for one enum value, or ``None`` when not confident.

    A key matches when its tail equals the value's name *and* everything in
    front of that tail belongs to the property name.  That head rule is what
    keeps ``middle`` (water level) away from ``dry_level_middle`` (即穿) and
    ``low`` away from the ``water_low`` fault blurb; among several valid
    candidates the one with the most specific head wins.
    """
    wanted = _normalise(label)
    if not texts or not wanted:
        return None
    prop_tokens = _tokens(prop_name)
    best = None
    for key, text in texts.items():
        clean = (text or "").strip()
        if not clean:
            continue
        parts = key.split("_")
        for index in range(len(parts)):
            tail = "_".join(parts[index:])
            if _normalise(tail) != wanted:
                continue
            head = _tokens("_".join(parts[:index]))
            if prop_tokens and head and not head.issubset(prop_tokens):
                break
            score = (len(head), -len(key))
            if best is None or score > best[0]:
                best = (score, key, clean)
            break
    return best[2] if best else None


def fault_notes(texts):
    """``[{code, title, method}]`` built from the plugin's fault strings.

    The plugin keeps pairs like ``temperature_sensor_problem`` =
    "温度传感器异常（tF）" and ``..._method`` = how to fix it; the washer alone
    ships a dozen of these.  Nothing in the MIoT spec carries them.
    """
    notes = []
    if not texts:
        return notes
    for key, title in sorted(texts.items()):
        if not (key.endswith("_problem") or key.endswith("_failed")):
            continue
        clean = " ".join((title or "").split())
        code = ""
        if clean.endswith("\uff09") and "\uff08" in clean:
            code = clean.split("\uff08")[-1][:-1].strip()
        # a real fault code is short (CF / bF / 3H / ~F); anything else is a
        # generic "load failed" style message, not a machine fault
        if not code or len(code) > 4:
            continue
        method = texts.get(key + "_method") or texts.get(key[:-8] + "_method") or ""
        notes.append({"code": code, "title": clean,
                      "method": " ".join(method.split())})
    return notes
