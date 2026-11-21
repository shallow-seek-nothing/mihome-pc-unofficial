"""Message centre: the flat list the UI renders.

``/v2/message/v2/typelist`` returns the newest message per type (that is why
the list is short); each record already carries a title, an optional body, a
device id and a timestamp.  Everything the UI needs is derived here so it can
be tested without a network.
"""
__all__ = ["normalise", "TYPE_LABELS"]

# Observed on a real account (2026-11-21): 6 = device alerts (cooking done,
# washing done, filter expired), 13 = scene/automation notices (run failed).
# Unknown types keep their number instead of being silently mislabelled.
TYPE_LABELS = {
    6: "\u8bbe\u5907\u6d88\u606f",
    11: "\u8bbe\u5907\u79bb\u7ebf",
    13: "\u573a\u666f\u901a\u77e5",
}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _message(record, devices):
    kind = _int(record.get("type"))
    did = str(record.get("did") or "")
    return {
        "id": str(record.get("msg_id") or ""),
        "type": kind,
        "type_label": TYPE_LABELS.get(kind, "\u6d88\u606f"),
        "title": record.get("title") or "",
        "content": record.get("content") or "",
        "did": did,
        "device": devices.get(did, "") if did else "",
        "time": _int(record.get("ctime")),
        "is_new": bool(_int(record.get("is_new"))),
        "image": record.get("img_url") or "",
    }


def normalise(payload, devices=None):
    """``{"messages": [...], "unread": n}`` newest first.

    ``devices`` maps did -> display name so the row can say which device it is.
    """
    data = payload if isinstance(payload, dict) else {}
    names = devices or {}
    messages = [_message(record, names)
                for record in data.get("messages") or ()
                if isinstance(record, dict) and record.get("msg_id")]
    messages.sort(key=lambda item: item["time"], reverse=True)
    # 按类型汇总，供页面上的筛选小标签用（多的排前面）
    counts = {}
    labels = {}
    for item in messages:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
        labels[item["type"]] = item["type_label"]
    types = [{"type": kind, "label": labels[kind], "count": count}
             for kind, count in sorted(counts.items(),
                                       key=lambda pair: (-pair[1], pair[0]))]
    return {"messages": messages,
            "types": types,
            "unread": sum(1 for item in messages if item["is_new"])}
