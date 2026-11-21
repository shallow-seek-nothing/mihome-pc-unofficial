"""Build a brand-new timer automation out of a template scene.

The scene service wants a *whole* scene record (trigger + action + bookkeeping)
and there is no documented "create" endpoint separate from ``Edit`` -- a
record without ``scene_id`` is created.  Rather than guessing which fields are
mandatory, we reuse a real record as the skeleton (any automation or manual
scene of the same home) and replace the interesting parts: name, timer
trigger, and actions.  That keeps us inside the shapes the server already
accepts.
"""
import copy

from . import timer as timer_mod

__all__ = ["build_action", "build_action_from", "build_clone_payload",
           "build_payload", "build_scene_payload", "build_update_payload",
           "user_trigger"]

# Fields inside one action record besides payload_json; copied from real
# records so the server sees the same bookkeeping it produced.
_ACTION_ENVELOPE = {
    "group_id": 0,
    "id": 0,
    "type": 0,
    "name": "",
    "payload": "",
    "protocol_type": 2,
    "sa_id": 0,
    "from": 0,
    "device_group_id": 0,
    "nested_scene_info": None,
    "std_sa_id": "",
    "option_ids": [],
    "data_ver": 0,
    "need_fill": 0,
}


def build_action_from(template_action, command, device, siid, piid=None,
                      aiid=None, value=None, device_name="", model="",
                      uid=""):
    """Clone a real action record and retarget it to another device/property.

    The server validates the action envelope hard (``-998
    CheckActionTypePayload``), and a hand-made envelope is rejected even when
    the payload looks right -- so keep every bookkeeping field the phone
    shipped (``sa_id``/``type``/``name``) and only swap what must change.
    """
    if not isinstance(template_action, dict) or not template_action:
        raise ValueError("\u7f3a\u5c11\u52a8\u4f5c\u6a21\u677f")
    action = copy.deepcopy(template_action)
    payload = action.get("payload_json")
    if not isinstance(payload, dict):
        raise ValueError("\u52a8\u4f5c\u6a21\u677f\u7f3a\u5c11 payload_json")
    did = str(device)
    payload["command"] = command
    payload["did"] = did
    payload["device_name"] = device_name or payload.get("device_name") or ""
    payload["model"] = model or payload.get("model") or ""
    payload["uid"] = str(uid or payload.get("uid") or "")
    payload["delay_time"] = 0
    if command == "action":
        payload["value"] = {"aiid": int(aiid), "did": did, "siid": int(siid),
                            "in": list(value or [])}
    else:
        payload["value"] = [{"did": did, "piid": int(piid), "siid": int(siid),
                             "value": value}]
    return action


def build_action(device, command, siid, piid=None, aiid=None, value=None,
                 device_name="", model="", uid="", order=1):
    """One action record for ``scene_action.actions``.

    ``command`` is ``set_properties`` (write one property) or ``action``
    (call a miot action).  The shapes match what the phone sends; the server
    rejects anything that drifts (``-8 data type not valid`` style).
    """
    did = str(device)
    payload = {
        "command": command,
        "delay_time": 0,
        "device_name": device_name,
        "did": did,
        "model": model,
        "uid": str(uid),
    }
    if command == "action":
        payload["value"] = {"aiid": int(aiid), "did": did, "siid": int(siid),
                            "in": list(value or [])}
    else:
        payload["value"] = [{"did": did, "piid": int(piid), "siid": int(siid),
                             "value": value}]
    action = dict(_ACTION_ENVELOPE)
    action["order"] = int(order)
    action["payload_json"] = payload
    return action


def _timer_trigger(hour, minute, label="", timezone="Asia/Shanghai",
                   days=None, kind=""):
    clock = "%02d:%02d" % (int(hour), int(minute))
    label = label or timer_mod.label_for(hour, minute, days, kind)
    return {
        "id": 0,
        "order": 1,
        "src": "timer",
        "key": "timer.crontab",
        "extra": "",
        "name": (label or clock),
        "value": "",
        "value_type": 5,
        "extra_json": {"filter": timer_mod.filter_for(kind),
                       "time_zone": timezone, "timer_from_miui": False},
        "value_json": timer_mod.cron_for(hour, minute, days),
        "protocol_type": 2,
        "sc_id": 12,
        "from": 0,
        "value_operation": 0,
        "std_sc_id": "",
        "duration": 0,
        "option_ids": [],
        "data_ver": 0,
        "need_fill": 0,
    }


# The manual-scene trigger, copied verbatim from a real record
# (\u89c2\u5f71\u6a21\u5f0f: src=user, key=user.click, sc_id=1).
_USER_TRIGGER = {
    "id": 1, "order": 1, "src": "user", "key": "user.click",
    "extra": "", "name": "", "value": "", "value_type": 5,
    "extra_json": {"voice_alias": {"alias": [], "use_title": 1}},
    "value_json": None, "protocol_type": 2, "sc_id": 1, "from": 0,
    "value_operation": 0, "std_sc_id": "", "duration": 0,
    "option_ids": [], "data_ver": 0, "need_fill": 0,
}


def user_trigger():
    """A fresh copy of the \u300c\u70b9\u51fb\u6267\u884c\u300d trigger (manual scenes)."""
    return copy.deepcopy(_USER_TRIGGER)


def _envelope(template, name, actions, home_id="", owner_uid=""):
    """Shared skeleton for a create-payload (see ``build_payload``)."""
    if not isinstance(template, dict) or not template:
        raise ValueError("\u9700\u8981\u4e00\u4e2a\u73b0\u6709\u573a\u666f\u4f5c\u4e3a\u6a21\u677f")
    if not actions:
        raise ValueError("\u81f3\u5c11\u9700\u8981\u4e00\u4e2a\u52a8\u4f5c")
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("\u573a\u666f\u540d\u5b57\u4e0d\u80fd\u4e3a\u7a7a")
    payload = copy.deepcopy(template)
    for field in ("scene_id", "uid", "create_time", "last_edit_time",
                  "icon_url", "first_trigger"):
        payload.pop(field, None)
    payload["name"] = clean_name
    payload["enable"] = True
    payload["home_id"] = str(home_id or payload.get("home_id") or "")
    payload["scene_condition"] = {"express": 0, "conditions": []}
    payload["scene_action"] = {"mode": 0, "actions": list(actions)}
    payload["scene_else_action"] = {"mode": 0, "actions": []}
    payload["edit_from"] = 1
    payload["value_format"] = 1
    payload["app_version"] = 19
    if owner_uid:
        payload["owner_uid"] = str(owner_uid)
    return payload


def build_update_payload(record, name=None, actions=None, home_id="",
                        owner_uid="", icon_url=None):
    """Edit an existing scene in place: keep its id and everything else.

    Only ``name`` and/or ``actions`` are replaced, so triggers, conditions and
    bookkeeping stay exactly as the server wrote them.
    """
    if not isinstance(record, dict) or not record.get("scene_id"):
        raise ValueError("\u7f3a\u5c11 scene_id\uff0c\u65e0\u6cd5\u7f16\u8f91")
    payload = copy.deepcopy(record)
    if name is not None:
        clean = str(name or "").strip()
        if not clean:
            raise ValueError("\u573a\u666f\u540d\u5b57\u4e0d\u80fd\u4e3a\u7a7a")
        payload["name"] = clean
    if actions:
        payload["scene_action"] = {"mode": 0, "actions": list(actions)}
    if icon_url is not None:
        payload["icon_url"] = icon_url
    payload["edit_from"] = 1
    payload["value_format"] = 1
    payload["app_version"] = 19
    if home_id:
        payload["home_id"] = str(home_id)
    if owner_uid:
        payload["owner_uid"] = str(owner_uid)
    return payload


def build_clone_payload(record, name, actions=None, home_id="", owner_uid=""):
    """Copy a scene/automation: same trigger, new name (and maybe new actions).

    The trigger is copied verbatim because the server validates device-event
    triggers against its own spec-scene table (``CheckTriggerConditionSrcKey...``
    rejects hand-made ones), so re-using a real trigger is the only reliable way
    to start from an existing automation.
    """
    payload = build_update_payload(record, name=name, actions=actions,
                                   home_id=home_id, owner_uid=owner_uid)
    payload.pop("scene_id", None)
    return payload


def build_scene_payload(template, name, actions, home_id="", owner_uid="",
                        icon_url=None):
    """A manual scene: same envelope, but the trigger is \u300c\u70b9\u51fb\u6267\u884c\u300d.

    ``template`` is any scene record; a manual scene already carries exactly
    this trigger, an automation gets it replaced.
    """
    payload = _envelope(template, name, actions, home_id, owner_uid)
    payload["scene_trigger"] = {"express": 1, "triggers": [user_trigger()]}
    if icon_url:
        payload["icon_url"] = icon_url
    return payload


def build_payload(template, name, hour, minute, actions, home_id="",
                  owner_uid="", label="", days=None, kind=""):
    """A create-payload: ``template`` is any ``GetSceneInfo`` reply.

    The template contributes field names and bookkeeping only; ``scene_id``
    is dropped so ``Edit`` creates a new record instead of touching it.
    """
    if not isinstance(template, dict) or not template:
        raise ValueError("\u9700\u8981\u4e00\u4e2a\u73b0\u6709\u573a\u666f\u4f5c\u4e3a\u6a21\u677f")
    if not actions:
        raise ValueError("\u81f3\u5c11\u9700\u8981\u4e00\u4e2a\u52a8\u4f5c")
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("\u573a\u666f\u540d\u5b57\u4e0d\u80fd\u4e3a\u7a7a")
    payload = copy.deepcopy(template)
    for field in ("scene_id", "uid", "create_time", "last_edit_time",
                  "icon_url", "first_trigger"):
        payload.pop(field, None)
    payload["name"] = clean_name
    payload["enable"] = True
    payload["home_id"] = str(home_id or payload.get("home_id") or "")
    trigger = _timer_trigger(hour, minute, label, days=days, kind=kind)
    payload["scene_trigger"] = {"express": 1, "triggers": [trigger]}
    payload["scene_condition"] = {"express": 0, "conditions": []}
    payload["scene_action"] = {"mode": 0, "actions": list(actions)}
    payload["scene_else_action"] = {"mode": 0, "actions": []}
    payload["edit_from"] = 1
    payload["value_format"] = 1
    payload["app_version"] = 19
    if owner_uid:
        payload["owner_uid"] = str(owner_uid)
    return payload
