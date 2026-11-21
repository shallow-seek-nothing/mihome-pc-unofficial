"""Pinned devices: a tiny list the UI keeps at the top of the grid."""
__all__ = ["order", "prune", "toggle"]


def toggle(pinned, did):
    """``(new_list, is_pinned_now)`` -- pin if absent, unpin if present."""
    did = str(did)
    items = [str(item) for item in (pinned or [])]
    if did in items:
        return [item for item in items if item != did], False
    return items + [did], True


def prune(pinned, known):
    """Drop ids that are no longer in the account (device removed)."""
    known = {str(item) for item in (known or [])}
    return [str(item) for item in (pinned or []) if str(item) in known]


def order(devices, pinned):
    """Pinned devices first, each group keeping the original order."""
    items = [str(item) for item in (pinned or [])]
    rank = {did: index for index, did in enumerate(items)}

    def key(device):
        did = str(device.get("did") or "")
        return (0, rank[did]) if did in rank else (1, 0)

    return sorted(devices, key=key)
