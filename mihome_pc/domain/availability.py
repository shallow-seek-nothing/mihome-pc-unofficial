"""Availability rules and the probe matrix that generates them.

Why this module exists
----------------------
v2 hand-wrote availability for 14 models and got it wrong: the dishwasher had
zero rules, so "mode" stayed clickable while the machine was off and the device
answered ``-5000``.  Hand-writing per model does not scale to 12k models and
cannot track firmware changes.

v3 therefore *measures*: send one write per (device state x control) and record
the device's answer.  The resulting matrix is turned into rules mechanically,
and each rule remembers where it came from, so a bad rule can be traced and
dropped.

Rules are plain data so they can be shipped, diffed and overridden::

    {"model": "xiaomi.dishwasher.s2",
     "blockers": [{"when": {"2.2": [6]},        # status == 6 (off)
                   "disabled": ["2.4"],          # mode
                   "reason": "关机状态下不可设置模式",
                   "source": "probe"}]}

The evaluator is intentionally dumb: it answers "is this control usable right
now, and if not, why" from the current state.  No spec knowledge, no I/O.
"""
import json
import os

__all__ = [
    "Blocker",
    "Availability",
    "ProbeStep",
    "ProbeMatrix",
    "plan_probe",
    "derive_blockers",
    "OK_CODE",
    "REJECT_CODES",
    "is_rejection",
]

OK_CODE = 0
# Device-side refusals we have actually seen while controlling real hardware.
REJECT_CODES = frozenset([-5000, -704053100, -704042011, -704030013])


def is_rejection(code):
    """True when the *device* refused the write.

    Measured on real hardware (dishwasher, 2026-11-19): writing ``mode`` while
    the appliance was running returned ``-704220043`` -- a code that appears in
    no document and in none of v2's notes.  A hard-coded list of "known"
    rejection codes therefore misses most real ones, so the rule is simply "any
    negative device code that is not one of the cloud's not-yet-confirmed
    signals".
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    return code < 0 and code not in UNCONFIRMED_CODES
# Cloud-side "the device did not confirm in time" -- not an availability fact.
UNCONFIRMED_CODES = frozenset([-704010000, -10000])

_MISSING = object()


def _key(address):
    """``"2.4"`` / ``(2, 4)`` / ``[2, 4]`` -> ``(2, 4)``."""
    if isinstance(address, tuple):
        return (int(address[0]), int(address[1]))
    if isinstance(address, str):
        left, _, right = address.partition(".")
        return (int(left), int(right))
    if isinstance(address, (list, tuple)) and len(address) == 2:
        return (int(address[0]), int(address[1]))
    raise ValueError("bad property address: %r" % (address,))


def format_key(key):
    return "%d.%d" % (key[0], key[1])


class Blocker:
    """Controls that stop working while some other properties hold given values."""

    __slots__ = ("when", "disabled", "reason", "source")

    def __init__(self, when, disabled, reason="", source="probe"):
        self.when = {_key(k): frozenset(v if isinstance(v, (list, tuple, set))
                                         else [v])
                         for k, v in when.items()}
        self.disabled = frozenset(_key(a) for a in disabled)
        self.reason = reason
        self.source = source

    def matches(self, state):
        """``state`` maps ``(siid, piid) -> value``."""
        for key, allowed in self.when.items():
            if key not in state:
                return False
            if state[key] not in allowed:
                return False
        return True

    def to_dict(self):
        return {
            "when": {format_key(k): sorted(v) for k, v in self.when.items()},
            "disabled": sorted(format_key(a) for a in self.disabled),
            "reason": self.reason,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("when") or {}, data.get("disabled") or [],
                   data.get("reason") or "", data.get("source") or "probe")

    def __repr__(self):
        return "<Blocker when=%r disables=%d>" % (self.to_dict()["when"],
                                                  len(self.disabled))


class Availability:
    """Evaluates blockers for one model against the current device state."""

    def __init__(self, model="", blockers=None):
        self.model = model
        self.blockers = list(blockers or ())

    def evaluate(self, state):
        """Return ``{(siid, piid): (enabled, reason)}`` for blocked controls only."""
        result = {}
        normalised = {_key(k): v for k, v in state.items()}
        for blocker in self.blockers:
            if not blocker.matches(normalised):
                continue
            for address in blocker.disabled:
                result[address] = (False, blocker.reason)
        return result

    def is_enabled(self, address, state):
        blocked = self.evaluate(state)
        key = _key(address)
        if key in blocked:
            return blocked[key][0]
        return True

    def reason_for(self, address, state):
        blocked = self.evaluate(state)
        return blocked.get(_key(address), (True, ""))[1]

    def add(self, blocker):
        self.blockers.append(blocker)

    def to_dict(self):
        return {"model": self.model,
                "blockers": [b.to_dict() for b in self.blockers]}

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("model") or "",
                   [Blocker.from_dict(b) for b in data.get("blockers") or ()])

    def save(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path):
        with open(path) as handle:
            return cls.from_dict(json.load(handle))


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------

class ProbeStep:
    """One planned write: set ``target`` while the device is in a given state."""

    __slots__ = ("target", "state_key", "state_value", "probe_value")

    def __init__(self, target, state_key, state_value, probe_value):
        self.target = _key(target)
        self.state_key = _key(state_key) if state_key else None
        self.state_value = state_value
        self.probe_value = probe_value

    def to_dict(self):
        return {"target": format_key(self.target),
                "state": (format_key(self.state_key) if self.state_key else ""),
                "state_value": self.state_value,
                "value": self.probe_value}

    def __repr__(self):
        return "<ProbeStep %s when %s=%r>" % (format_key(self.target),
                                              format_key(self.state_key)
                                              if self.state_key else "-",
                                              self.state_value)


def plan_probe(spec, states=None, max_targets=None, skip=(), current=None,
               mutate=False):
    """Plan the write matrix: every (state value x writable control).

    ``states`` is a list of ``(address, value)`` pairs describing the device
    states to visit; when omitted it is derived from the spec (``on`` bool, or
    the ``status`` enum).

    ``current`` maps ``(siid, piid) -> value`` for the device's present state.
    When supplied, each step writes the value the property **already has**, so
    the probe cannot change the appliance -- it only reveals whether the device
    accepts a write at all right now.  Without it we have to guess a value, which
    is why passing ``current`` is strongly preferred.
    """
    skip_keys = {_key(a) for a in skip}
    if states is None:
        states = _default_states(spec)

    current = {_key(k): v for k, v in (current or {}).items()} or None

    targets = [p for p in spec.writable_properties()
               if (p.siid, p.piid) not in skip_keys]
    # Prefer interesting controls: enums and numbers over plain bools, because a
    # refused bool usually just means the device is off.
    targets.sort(key=lambda p: (1 if p.format == "bool" else 0, p.siid, p.piid))
    if max_targets:
        targets = targets[:max_targets]

    steps = []
    for address, value in states:
        for prop in targets:
            if (prop.siid, prop.piid) == _key(address):
                continue                      # do not fight the state variable
            steps.append(ProbeStep((prop.siid, prop.piid), address, value,
                                   _probe_value_for(prop, current, mutate)))
    return steps


def _default_states(spec):
    power = spec.power_property()
    if power is not None:
        return [((power.siid, power.piid), False), ((power.siid, power.piid), True)]
    status = spec.status_property()
    if status is not None and status.value_list:
        address = (status.siid, status.piid)
        return [(address, option.value) for option in status.value_list]
    return []


def _probe_value_for(prop, current=None, mutate=False):
    """Value to attempt in a probe.

    ``mutate=False`` writes the value the property already holds.  That is safe
    but **useless as evidence**: measured on a real heater (xiaomi.heater.ma7),
    every no-op write came back ``code 0`` in both power states, because the
    device short-circuits an unchanged value.  To find out whether a control is
    really usable in a given state you have to ask for a different value.

    ``mutate=True`` therefore picks the smallest legal step away from the
    current value: negate a bool, next enum option, one range step, +1 for a
    bare number.
    """
    if mutate:
        return _mutated_value(prop, current)
    if current:
        existing = current.get((prop.siid, prop.piid), _MISSING)
        if existing is not _MISSING and existing is not None:
            try:
                return prop.coerce(existing)
            except (TypeError, ValueError):
                pass
    if prop.format == "bool":
        return True
    if prop.value_list:
        return prop.value_list[0].value
    if prop.value_range:
        return prop.value_range[0]
    if prop.format.startswith(("uint", "int", "float")):
        return 1
    return "0"


def _mutated_value(prop, current=None):
    """Closest legal value that differs from the current one."""
    existing = (current or {}).get((prop.siid, prop.piid), _MISSING)
    if prop.format == "bool":
        if existing is _MISSING or existing is None:
            return True
        try:
            return not prop.coerce(existing)
        except (TypeError, ValueError):
            return True
    if prop.value_list:
        values = [option.value for option in prop.value_list]
        if existing in values and len(values) > 1:
            position = values.index(existing)
            return values[(position + 1) % len(values)]
        return values[0]
    if prop.value_range:
        low, high, step = prop.value_range
        base = existing if isinstance(existing, (int, float)) else low
        candidate = base + (step or 1)
        if candidate > high:
            candidate = base - (step or 1)
        return max(low, min(high, candidate))
    if prop.format.startswith(("uint", "int", "float")):
        base = existing if isinstance(existing, (int, float)) else 0
        return base + 1
    if isinstance(existing, str):
        return existing + "x" if existing else "0"
    return "0"


class ProbeMatrix:
    """Collects ``(target, state) -> device code`` (plus read-back) and derives blockers."""

    def __init__(self, model=""):
        self.model = model
        self.samples = []          # (target, state_key, state_value, code, applied)

    def record(self, target, state_key, state_value, code, applied=None):
        """Add one measurement.

        ``applied`` is the result of reading the property back after the write.
        A real washer answered ``code 0`` and then reported an unchanged value
        (measured 2026-11-19, minij.washer.v5 while a cycle was running), so a
        return code alone cannot prove that a control is usable.  Pass
        ``True``/``False`` when a read-back was performed; ``None`` means "not
        measured" and the code-based heuristics apply.
        """
        self.samples.append((_key(target),
                             _key(state_key) if state_key else None,
                             state_value, int(code), applied))

    # -- analysis ------------------------------------------------------
    def outcome(self, target):
        """``{(state_key, state_value): bool}`` -- True when the write took effect."""
        table = {}
        for tgt, state_key, state_value, code, applied in self.samples:
            if tgt != _key(target):
                continue
            table[(state_key, state_value)] = (applied if applied is not None
                                               else code == OK_CODE)
        return table

    def rejected_states(self):
        """``{(siid,piid): {state_value: True}}`` for consistently refused writes."""
        per_target = {}
        for tgt, _state_key, state_value, code, applied in self.samples:
            entry = per_target.setdefault(tgt, {})
            refused = (not applied) if applied is not None else is_rejection(code)
            entry[state_value] = entry.get(state_value, True) and refused
        return {k: v for k, v in per_target.items() if any(v.values())}

    def always_failing_targets(self):
        """Targets refused in *every* state -- these need a human, not a rule."""
        table = {}
        for tgt, _, _, code, applied in self.samples:
            stats = table.setdefault(tgt, [0, 0])
            stats[0] += 1
            bad = (applied is False) if applied is not None else is_rejection(code)
            if bad:
                stats[1] += 1
        return sorted(format_key(t) for t, (total, bad) in table.items()
                      if total and bad == total)


def derive_blockers(matrix, state_key=None,
                    reason_template="\u5f53\u524d\u72b6\u6001\u4e0b\u4e0d\u53ef\u8bbe\u7f6e\u300c%s\u300d"):
    """Turn a probe matrix into :class:`Blocker` objects.

    A blocker is emitted for a target only when the device let the write take
    effect in *at least one* state and refused it in the others -- otherwise we
    cannot tell "disabled" from "the device simply dislikes that value".

    "Took effect" prefers the read-back result (``applied``) and falls back to
    ``code == 0`` when no read-back was recorded.
    """
    blockers = []
    if state_key is None:
        state_key = _infer_state_key(matrix)
    if state_key is None:
        return blockers

    # target -> state_value -> [(code, applied), ...]
    per_target = {}
    for target, sample_key, state_value, code, applied in matrix.samples:
        if sample_key != state_key:
            continue
        per_target.setdefault(target, {}).setdefault(state_value, []).append(
            (code, applied))
    if not per_target:
        return blockers

    for target, by_state in per_target.items():
        worked = set()
        refused = set()
        for state_value, samples in by_state.items():
            if any(applied is True for _, applied in samples):
                worked.add(state_value)
            elif any(applied is False for _, applied in samples):
                refused.add(state_value)
            elif any(code == OK_CODE for code, applied in samples if applied is None):
                worked.add(state_value)
            elif any(is_rejection(code) for code, applied in samples
                     if applied is None):
                refused.add(state_value)
        if not worked or not refused:
            continue
        disabled_states = sorted(refused - worked)
        if not disabled_states:
            continue
        label = "%d.%d" % target
        blockers.append(Blocker(
            when={state_key: disabled_states},
            disabled=[target],
            reason=reason_template % label,
            source="probe",
        ))
    return blockers


def _infer_state_key(matrix):
    keys = set()
    for _, state_key, _, _, _ in matrix.samples:
        if state_key is not None:
            keys.add(state_key)
    if len(keys) == 1:
        return keys.pop()
    return None
