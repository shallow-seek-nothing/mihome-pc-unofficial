"""Home sharing members (read-only).

``/v2/homeroom/member_list`` answers two parallel lists: ``list`` (permission)
and ``userdetail`` (nicknames).  This module joins them into one row per
member so the UI does not have to.
"""
__all__ = ["normalise", "ROLE_LABELS"]

ROLE_LABELS = {
    "owner": "\u62e5\u6709\u8005",
    "admin": "\u7ba1\u7406\u5458",
    "member": "\u6210\u5458",
}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalise(payload, owner_uid=None):
    """``{"members": [...]}`` with name, role and join time filled in."""
    data = payload if isinstance(payload, dict) else {}
    details = {}
    for item in data.get("userdetail") or ():
        if isinstance(item, dict):
            details[_int(item.get("uid"))] = item
    members = []
    for item in data.get("list") or ():
        if not isinstance(item, dict):
            continue
        uid = _int(item.get("uid"))
        detail = details.get(uid) or {}
        permit = _int(item.get("permit_level"))
        if owner_uid and uid == _int(owner_uid):
            role = "owner"
        elif permit >= 10:
            role = "admin"
        else:
            role = "member"
        members.append({
            "uid": uid,
            "name": (detail.get("nick_name") or item.get("invite_nick")
                     or "uid %d" % uid),
            "icon": detail.get("icon") or "",
            "role": role,
            "role_label": ROLE_LABELS[role],
            "permit_level": permit,
            "joined": _int(item.get("invite_time")),
            "note": item.get("note") or "",
        })
    members.sort(key=lambda member: (-member["permit_level"], member["uid"]))
    return {"members": members, "count": len(members)}
