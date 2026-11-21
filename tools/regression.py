"""Read-only regression sweep against a running server.

It touches nothing: every check is a GET, so it is safe to run against the
real account (it does mutate nothing) or against the demo instance.

    python tools/regression.py                      # http://127.0.0.1:18780
    python tools/regression.py --port 18791 --demo
"""
import argparse
import json
import sys

try:
    from urllib.request import urlopen
except ImportError:                                  # pragma: no cover
    from urllib2 import urlopen

# (name, path, checker) -- checker(payload) returns a short fact string or
# raises AssertionError.  Keep this list in step with docs/04-功能对照.md.
CHECKS = [
    ("status", "/api/status",
     lambda p: "demo" if p.get("demo") else "\u5df2\u767b\u5f55"),
    ("meta", "/api/meta",
     lambda p: "v%s" % (p.get("version") or "?")),
    ("devices", "/api/devices",
     lambda p: "%d \u53f0\u8bbe\u5907 / %d \u4e2a\u623f\u95f4"
               % (len(p.get("devices") or []), len(p.get("rooms") or []))),
    ("scenes", "/api/scenes",
     lambda p: "%d \u573a\u666f / %d \u81ea\u52a8\u5316"
               % (len(p.get("scenes") or []), len(p.get("automations") or []))),
    ("messages", "/api/messages",
     lambda p: "%d \u6761\u6d88\u606f / %d \u672a\u8bfb"
               % (len(p.get("messages") or []), p.get("unread") or 0)),
    ("members", "/api/members",
     lambda p: "%d \u4f4d\u6210\u5458" % (p.get("count") or 0)),
    ("favorites", "/api/favorites",
     lambda p: "%d \u53f0\u7f6e\u9876" % len(p.get("dids") or [])),
    ("scene icons", "/api/scene/icons",
     lambda p: "%d \u4e2a\u56fe\u6807" % len(p.get("icons") or [])),
]


def fetch(base, path, timeout=30):
    with urlopen(base + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run(base, checks=None):
    """``[(name, ok, detail)]`` -- one row per check, never raises.

    Device/scene specific checks are appended when the lists are non-empty,
    so a fresh (empty) account still produces a meaningful report.
    """
    rows = []
    payloads = {}
    for name, path, checker in (checks or CHECKS):
        try:
            payload = fetch(base, path)
        except Exception as exc:            # noqa: BLE001 - report, do not raise
            rows.append((name, False, "%s" % str(exc)[:80]))
            continue
        payloads[name] = payload
        try:
            rows.append((name, True, checker(payload)))
        except Exception as exc:            # noqa: BLE001
            rows.append((name, False, "\u683c\u5f0f\u4e0d\u5bf9\uff1a%s" % str(exc)[:60]))
    devices = (payloads.get("devices") or {}).get("devices") or []
    # 挑一台真正能打开的设备（手环这类没 spec 的会 404，那是正确行为）
    usable = [item for item in devices
              if item.get("did") and not item.get("error")
              and (item.get("controls") or item.get("primary"))]
    if devices:
        did = (usable or devices)[0].get("did")
        for name, path, checker in (
            ("device detail", "/api/device?did=" + str(did),
             lambda p: "%d \u4e2a\u63a7\u4ef6" % len(p.get("controls") or [])),
            ("device info", "/api/device/info?did=" + str(did),
             lambda p: "%s / %s" % (p.get("model") or "?",
                                     (p.get("info") or {}).get("firmware-revision")
                                     or "?\u56fa\u4ef6")),
            ("device messages", "/api/messages/device?did=" + str(did),
             lambda p: "%d \u6761\u5386\u53f2" % len(p.get("messages") or [])),
        ):
            try:
                rows.append((name, True, checker(fetch(base, path))))
            except Exception as exc:        # noqa: BLE001
                rows.append((name, False, str(exc)[:80]))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18780)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    base = "http://%s:%d" % (args.host, args.port)
    rows = run(base)
    for name, ok, detail in rows:
        print("  %s %-16s %s" % ("\u2713" if ok else "\u2717", name, detail))
    failed = [name for name, ok, _d in rows if not ok]
    print("regression: %d checks, %d failed" % (len(rows), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
