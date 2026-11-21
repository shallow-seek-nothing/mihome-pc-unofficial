"""Cross-property ranges: one property's limit depends on another's value.

Real case: the careli air fryer accepts up to 1440 minutes of cook time at low
temperature, but only a fraction of that when hot.  The spec publishes a flat
1..1440 range, so the UI happily offered 24 hours at 180 C and the device then
rejected the write.

The table below is produced by measurement (tools/probe_fryer_limits.py), in the
same spirit as availability rules: probe the hardware, write down what it does.
"""
import json
import os

__all__ = ["load_limits", "dynamic_range", "explain", "check_value"]

_LIMITS = None
_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "assets", "dynamic_ranges.json")


def load_limits(reload=False):
    """``{model: {"2.3": {"depends_on": "2.4", "bands": [[lo, hi, max], ...]}}}``"""
    global _LIMITS
    if _LIMITS is not None and not reload:
        return _LIMITS
    table = {}
    try:
        with open(_PATH, encoding="utf-8") as handle:
            table = json.load(handle) or {}
    except (OSError, ValueError):
        table = {}
    _LIMITS = table
    return table


def _band_for(rule, dependency_value):
    try:
        value = float(dependency_value)
    except (TypeError, ValueError):
        return None
    for band in rule.get("bands") or ():
        if len(band) < 3:
            continue
        low, high, limit = band[0], band[1], band[2]
        if low <= value <= high:
            return limit
    return None


def dynamic_range(model, address, state):
    """Replacement ``[min, max, step]`` for one control, or ``None``."""
    rule = (load_limits().get(model) or {}).get(address)
    if not rule:
        return None
    dependency = state.get(rule.get("depends_on") or "")
    limit = _band_for(rule, dependency)
    if limit is None:
        return None
    return [1, int(limit), 1]


def explain(model, address, state):
    """Human sentence for the constraint, or ``""``."""
    rule = (load_limits().get(model) or {}).get(address)
    if not rule:
        return ""
    limit = _band_for(rule, state.get(rule.get("depends_on") or ""))
    if limit is None:
        return ""
    label = rule.get("label") or "\u65f6\u95f4"
    dep_label = rule.get("depends_label") or "\u6e29\u5ea6"
    unit = rule.get("unit") or "\u5206\u949f"
    return "%s\u4e0b%s\u6700\u957f %s %s" % (dep_label, label, limit, unit)


def check_value(model, address, value, state):
    """``None`` when fine, else ``(limit, message)``."""
    rule = (load_limits().get(model) or {}).get(address)
    if not rule:
        return None
    limit = _band_for(rule, state.get(rule.get("depends_on") or ""))
    if limit is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= limit:
        return None
    return limit, explain(model, address, state)
