"""Command line entry points.

Phase 1 is a *protocol* CLI: it exists so the reverse-engineered layers can be
exercised against the real account before any UI is built.

    python -m mihome_pc login          # QR login, stores credentials
    python -m mihome_pc refresh        # renew serviceToken from passToken
    python -m mihome_pc devices        # list devices (with catalog names)
    python -m mihome_pc view DID       # spec-generated controls for one device
    python -m mihome_pc props DID ...  # read properties
    python -m mihome_pc spec DID       # fetch + parse a device spec
    python -m mihome_pc probe DID      # availability matrix probe (dry run)
    python -m mihome_pc serve          # local web UI (demo data if not logged in)
    python -m mihome_pc selftest       # offline checks, no account needed
"""
import argparse
import json
import os
import sys
import tempfile
import time

from .xiaomi import auth, cloud, spec as spec_mod

DEFAULT_TIMEOUT = 20


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def _ensure_dir(path):
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
        return True
    except OSError:
        return False


def data_dir():
    """Per-user writable dir, with a fallback when the home dir is unusable.

    ``%LOCALAPPDATA%`` is redirected for Store-packaged Python, and a sandboxed
    or service context may not be allowed to write under the user profile at
    all -- which is exactly what happened the first time the login tried to save
    credentials here.  Falling back to ``<install dir>/data`` keeps the app
    working instead of dying on the first write.
    """
    candidate = os.environ.get("MIHOME_PC_HOME")
    if candidate and _ensure_dir(candidate):
        return candidate
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(app_root, "portable.flag")):
        # portable (green) build: keep everything inside the app folder
        portable = os.path.join(app_root, "data")
        if _ensure_dir(portable):
            return portable
    preferred = os.path.join(os.path.expanduser("~"), ".mihome-pc")
    if _ensure_dir(preferred):
        return preferred
    fallback = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    if _ensure_dir(fallback):
        return fallback
    return preferred


def credentials_path():
    return os.path.join(data_dir(), "credentials.json")


def spec_cache_dir():
    directory = os.path.join(data_dir(), "specs")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    return directory


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _catalog():
    """Lazy-load the extracted model catalog (2.7 MB, only when needed)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "catalog.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _open_file(path):
    """Best effort: show the QR image with the OS default viewer."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)          # noqa: S606 - deliberate
            return True
        if sys.platform == "darwin":
            os.system("open '%s' >/dev/null 2>&1" % path)
            return True
        os.system("xdg-open '%s' >/dev/null 2>&1 &" % path)
        return True
    except Exception:                    # noqa: BLE001 - never fatal
        return False


def _download(url, target, timeout=DEFAULT_TIMEOUT):
    from urllib.request import urlopen
    response = urlopen(url, timeout=timeout)
    data = response.read()
    with open(target, "wb") as handle:
        handle.write(data)
    return len(data)


def _session_from_credentials(credentials):
    return cloud.Session(
        user_id=credentials.user_id,
        c_user_id=credentials.c_user_id,
        service_token=credentials.service_token,
        ssecurity=credentials.ssecurity,
        device_id=credentials.device_id,
    )


def _load_session():
    credentials = auth.load_credentials(credentials_path())
    if credentials is None:
        raise SystemExit("not logged in; run: python -m mihome_pc login")
    return credentials, cloud.CloudClient(_session_from_credentials(credentials))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_login(args):
    login = auth.QrLogin(sid=auth.SID_MIHOME)
    qr_url, lp_url, login_url = login.request_qr()

    target = os.path.join(tempfile.gettempdir(), "mihome-pc-qr.png")
    try:
        size = _download(qr_url, target)
    except Exception as exc:             # noqa: BLE001
        size = 0
        print("warning: could not download the QR image (%s)" % exc)

    print("请用米家 App 扫描二维码：")
    if size:
        print("  图片: %s" % target)
        if _open_file(target):
            print("  （已尝试用系统图片查看器打开）")
    print("  或在浏览器打开这个链接完成登录：")
    print("  %s" % login_url)
    print("")
    print("等待手机确认…（最多 %d 秒）" % args.timeout)
    sys.stdout.flush()

    credentials = login.wait_for_scan(timeout=args.timeout)
    auth.save_credentials(credentials, credentials_path())
    print("登录成功：userId=%s" % credentials.user_id)
    print("凭据已写入 %s" % credentials_path())

    client = cloud.CloudClient(_session_from_credentials(credentials))
    _print_connection_report(client)
    return 0


def _print_connection_report(client):
    """Tell the user which base URL actually answered -- the key open question."""
    print("")
    print("网关连通性：")
    for base in (cloud.BASE_CN, cloud.BASE_IO):
        probe = cloud.CloudClient(client.session, base_url=base)
        try:
            probe.homes()
            print("  OK   %s" % base)
        except Exception as exc:         # noqa: BLE001
            print("  FAIL %s -- %s" % (base, exc))


def cmd_refresh(args):
    credentials = auth.load_credentials(credentials_path())
    if credentials is None:
        raise SystemExit("not logged in; run: python -m mihome_pc login")
    refreshed = auth.refresh_service_token(credentials)
    auth.save_credentials(refreshed, credentials_path())
    print("serviceToken 已续期")
    return 0


def cmd_devices(args):
    credentials, client = _load_session()
    result = client.devices()
    catalog = _catalog()
    devices = _extract_devices(result)
    print("共 %d 台设备" % len(devices))
    for device in devices:
        model = device.get("model") or ""
        name = device.get("name") or ""
        entry = catalog.get(model, {})
        label = entry.get("n") or ""
        urn = device.get("spec_type") or device.get("spec_urn") or ""
        print("  %-22s %-22s %-14s %s"
              % (device.get("did", ""), model, "online" if device.get("isOnline") else "offline",
                 label or name))
        if args.verbose:
            print("      urn=%s localip=%s" % (urn, device.get("localip") or "-"))
    return 0


def _extract_devices(result):
    """The device list endpoint wraps records a couple of different ways."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in ("list", "devices", "device_info"):
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def cmd_props(args):
    _, client = _load_session()
    items = []
    for raw in args.props:
        parts = raw.replace(":", ".").split(".")
        if len(parts) != 2:
            raise SystemExit("property must look like SIID.PIID, got %r" % raw)
        items.append((args.did, int(parts[0]), int(parts[1])))
    result = client.props(items)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def cmd_spec(args):
    """Dump a device spec. A did goes through the gateway (which resolves the
    urn); a urn goes through the public mirror."""
    credentials, client = _load_session()
    target = args.target
    if ":" not in target:
        payload = client.specs([target])
        specs = extract_instances(payload)
        spec = specs.get(target) or _first_spec(payload)
        if spec is None:
            raise SystemExit("gateway returned no spec for did %s" % target)
        parsed = spec_mod.parse_spec(spec)
        _apply_i18n_for(parsed)
        print("urn: %s" % parsed.urn)
        print(json.dumps(parsed.to_dict(), ensure_ascii=False, indent=1))
        return 0
    parsed = _fetch_spec(target, client)
    print(json.dumps(parsed.to_dict(), ensure_ascii=False, indent=1))
    return 0


def _apply_i18n_for(parsed):
    """Best-effort Chinese labels for a spec that came from the gateway."""
    path = os.path.join(spec_cache_dir(), parsed.urn.replace(":", "_") + ".i18n.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            spec_mod.apply_i18n(parsed, json.load(handle))
        return
    try:
        table = cloud.fetch_spec_i18n(parsed.urn)
    except cloud.CloudError:
        return
    if not table:
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(table, handle, ensure_ascii=False)
    spec_mod.apply_i18n(parsed, table)


def extract_instances(payload):
    """Normalise `get_instances_by_dids` replies into `{did: spec_dict}`.

    Confirmed against the live gateway: sending a did with an **empty urn** makes
    the server resolve the urn itself, and the reply looks like::

        {"instances": {"<did>": {"urn": "urn:miot-spec-v2:...",
                                 "isSpecv3": false,
                                 "instance": "<full spec as a JSON string>"}}}

    Note `instance` is a *string*, and `instances` is keyed by did rather
    than being a list -- both are easy to get wrong.
    """
    result = {}
    if not isinstance(payload, dict):
        return result
    instances = payload.get("instances")
    if isinstance(instances, dict):
        entries = instances.items()
    elif isinstance(instances, list):
        entries = [(str(i), entry) for i, entry in enumerate(instances)]
    else:
        entries = []
    for did, entry in entries:
        if not isinstance(entry, dict):
            continue
        spec = entry.get("instance")
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                continue
        if isinstance(spec, dict) and spec.get("services"):
            result[str(did)] = spec
    if not result and isinstance(payload.get("services"), list):
        result[""] = payload                 # already a bare instance
    return result


def _first_spec(payload):
    specs = extract_instances(payload)
    if specs:
        return list(specs.values())[0]
    return None


def _fetch_spec(urn, client=None):
    """Cache-first spec lookup (instance + i18n), Chinese labels by default."""
    return cloud.load_spec(urn, spec_cache_dir())


def _find_device(client, did):
    result = client.devices()
    for device in _extract_devices(result):
        if device.get("did") == did:
            return device
    raise SystemExit("device %s not found in this account" % did)


def _read_values(client, did, spec):
    """Current values for every readable property, as ``{(siid,piid): value}``."""
    items = [(did, p.siid, p.piid) for p in spec.readable_properties()]
    if not items:
        return {}
    payload = client.props(items)
    values = {}
    for record in cloud.property_records(payload):
        if record.get("code") == 0:
            values[(record["siid"], record["piid"])] = record.get("value")
    return values


def _result_code(payload):
    """Code of a ``/miotspec/prop/set`` reply, whatever shape it arrives in."""
    return cloud.first_code(payload)


def cmd_view(args):
    """Show the generated controls for one device (spec driven)."""
    from .domain import model as device_model
    _, client = _load_session()
    device = _find_device(client, args.did)
    urn = device.get("spec_type") or device.get("spec_urn") or ""
    if not urn:
        raise SystemExit("device has no spec_type; cannot build controls")
    spec = _fetch_spec(urn, client)
    view = device_model.DeviceView(
        did=device.get("did"), model=device.get("model") or "",
        name=device.get("name") or "", online=bool(device.get("isOnline")),
        spec=spec, catalog=device_model.load_catalog_entry(device.get("model") or ""),
        layout=device_model.load_card_layout(device.get("model") or ""))
    print(json.dumps(view.to_dict(), ensure_ascii=False, indent=1))
    return 0


def _read_back(client, did, target, expected):
    """True when the property really holds ``expected`` after the write."""
    payload = client.props([(did, target[0], target[1])])
    for record in cloud.property_records(payload):
        if record.get("code") in (0, None):
            return record.get("value") == expected
    return False


def cmd_probe(args):
    """Measure which controls the device actually accepts, per state.

    Writes are *no-op* by default (the value the property already holds), so the
    appliance is not actuated; only ``--commit`` allows real changes.
    """
    from .domain import availability
    _, client = _load_session()
    device = _find_device(client, args.did)
    model_name = device.get("model") or ""
    urn = device.get("spec_type") or ""
    if not urn:
        raise SystemExit("device has no spec_type")
    spec = _fetch_spec(urn, client)

    current = _read_values(client, args.did, spec)
    states = None
    if not getattr(args, "allow_transition", False):
        # SAFE BY DEFAULT. Probing only the current state means every write puts
        # the value back unchanged, so nothing on the appliance can move.
        # Switching states requires --allow-transition, because an earlier
        # version of this command silently turned a user's dishwasher on.
        power = spec.power_property()
        key = (power.siid, power.piid) if power is not None else None
        states = [(key, current.get(key) if key else None)]
    steps = availability.plan_probe(spec, states=states, current=current,
                                    max_targets=args.max_targets,
                                    mutate=getattr(args, "mutate", False))
    if not steps:
        print("nothing to probe (no writable controls besides the state variable)")
        return 0

    switching = bool(getattr(args, "allow_transition", False))
    print("model %s  controls %d  current values %d"
          % (model_name, len(spec.writable_properties()), len(current)))
    print("mode: %s" % ("SWITCHING STATES (device will be toggled)"
                        if switching else "current state only (no state change)"))
    for step in steps:
        print("  plan: write %s = %r while %s=%r"
              % (availability.format_key(step.target), step.probe_value,
                 availability.format_key(step.state_key), step.state_value))
    if not args.commit:
        print("")
        print("dry run: nothing was written. Re-run with --commit to measure.")
        return 0

    matrix = availability.ProbeMatrix(model_name)
    for state_value in sorted({s.state_key and s.state_value for s in steps},
                              key=lambda v: (v is None, str(v))):
        if state_value is None:
            continue
        state_key = [s.state_key for s in steps if s.state_value == state_value][0]
        print("entering state %s=%r"
              % (availability.format_key(state_key), state_value))
        client.set_props([(args.did, state_key[0], state_key[1], state_value)])
        time.sleep(args.settle)
        for step in [s for s in steps if s.state_value == state_value]:
            payload = client.set_props(
                [(args.did, step.target[0], step.target[1], step.probe_value)])
            code = _result_code(payload)
            # Read back: a device may answer code 0 and silently ignore the
            # write (measured on minij.washer.v5 while a cycle was running), so
            # "applied" -- not the return code -- is what proves usability.
            time.sleep(args.readback_delay)
            applied = _read_back(client, args.did, step.target, step.probe_value)
            matrix.record(step.target, step.state_key, state_value, code, applied)
            print("  %s -> code %s applied=%s"
                  % (availability.format_key(step.target), code, applied))

    blockers = availability.derive_blockers(matrix)
    evaluator = availability.Availability(model_name, blockers)
    for blocker in blockers:
        print("rule: %s" % json.dumps(blocker.to_dict(), ensure_ascii=False))
    failing = matrix.always_failing_targets()
    if failing:
        print("always refused (needs a human): %s" % ", ".join(failing))

    if args.out:
        evaluator.save(args.out)
        print("saved %s" % args.out)
    return 0


def cmd_serve(args):
    """Start the local web UI (demo data when not logged in)."""
    from .web import server as web_server
    return web_server.serve(port=args.port, open_browser=not args.no_browser)


def cmd_app(args):
    """Desktop mode: local server + tray icon (right-click to quit)."""
    from . import app as app_mod
    return app_mod.run(port=args.port, open_browser=not args.no_browser)

def environment_report(data_path=None, plugin_root=None):
    """Where things live and whether they are usable (for bug reports).

    Returns ``[(name, status, detail)]`` with status in ok/warn/off so the
    CLI can print a truthful summary instead of a bare \u201cok\u201d.
    """
    data_path = data_path or data_dir()
    plugin_root = plugin_root or os.path.join(data_path, "plugins")
    rows = []

    writable = False
    try:
        os.makedirs(data_path, exist_ok=True)
        probe = os.path.join(data_path, ".selftest-probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        writable = True
    except OSError as exc:
        rows.append(("\u6570\u636e\u76ee\u5f55", "warn", "%s (%s)" % (data_path, exc)))
    if writable:
        rows.append(("\u6570\u636e\u76ee\u5f55", "ok", data_path))

    credentials = os.path.join(data_path, "credentials.json")
    if os.path.exists(credentials):
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(
            os.path.getmtime(credentials)))
        rows.append(("\u51ed\u636e", "ok", "\u5df2\u767b\u5f55\uff08%s\uff09" % stamp))
    else:
        rows.append(("\u51ed\u636e", "off",
                     "\u672a\u767b\u5f55\uff08\u6f14\u793a\u6a21\u5f0f\uff09"))

    specs = os.path.join(data_path, "specs")
    count = 0
    if os.path.isdir(specs):
        count = len([n for n in os.listdir(specs) if n.endswith(".json")])
    rows.append(("spec \u7f13\u5b58", "ok" if count else "off", "%d \u4e2a\u6587\u4ef6" % count))

    texts = 0
    if os.path.isdir(plugin_root):
        for name in os.listdir(plugin_root):
            cache = os.path.join(plugin_root, name, "texts.json")
            if os.path.exists(cache):
                texts += 1
    rows.append(("\u63d2\u4ef6\u6587\u6848", "ok" if texts else "off",
                 "%d \u4e2a\u578b\u53f7\u5df2\u7f13\u5b58" % texts))
    return rows


def cmd_selftest(args):
    """Offline sanity checks: catalog + a bundled spec + crypto vectors.

    With ``--env`` it also prints where the data lives and what is usable.
    """
    print("catalog: %d models" % len(_catalog()))
    from .crypto import aes, rc4
    from .xiaomi import sign
    assert aes.AES(b"\x00" * 16).encrypt_block(b"\x01" * 16)
    assert rc4.RC4(b"Key").crypt(b"Plaintext")
    assert sign.gen_nonce()
    print("crypto: ok")
    fixtures = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tests", "fixtures", "specs")
    if os.path.isdir(fixtures):
        spec_files = [n for n in sorted(os.listdir(fixtures))
                      if n.endswith('.json') and not n.endswith('.i18n.json')]
        for name in spec_files[:3]:
            with open(os.path.join(fixtures, name), encoding="utf-8") as handle:
                parsed = spec_mod.parse_spec(json.load(handle))
            print("spec %-24s %d services" % (name, len(parsed.services)))
    if getattr(args, "env", False):
        print("")
        print("\u73af\u5883\uff1a")
        for name, status, detail in environment_report():
            mark = {"ok": "\u2713", "warn": "!", "off": "-"}[status]
            print("  %s %s\uff1a%s" % (mark, name, detail))
    print("selftest: ok")
    return 0


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="mihome_pc", description="米家 PC 客户端（协议层 CLI）")
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser("login", help="扫码登录并保存凭据")
    login.add_argument("--timeout", type=int, default=180, help="等待扫码的秒数")
    login.set_defaults(func=cmd_login)

    refresh = sub.add_parser("refresh", help="用 passToken 续期 serviceToken")
    refresh.set_defaults(func=cmd_refresh)

    devices = sub.add_parser("devices", help="列出设备")
    devices.add_argument("-v", "--verbose", action="store_true")
    devices.set_defaults(func=cmd_devices)

    props = sub.add_parser("props", help="读取属性")
    props.add_argument("did")
    props.add_argument("props", nargs="+", help="SIID.PIID，可多个")
    props.set_defaults(func=cmd_props)

    view_cmd = sub.add_parser("view", help="生成并打印某台设备的控件（spec 驱动）")
    view_cmd.add_argument("did")
    view_cmd.set_defaults(func=cmd_view)

    probe_cmd = sub.add_parser("probe", help="矩阵探测设备可用性（默认只演练不写入）")
    probe_cmd.add_argument("did")
    probe_cmd.add_argument("--max-targets", type=int, default=None)
    probe_cmd.add_argument("--settle", type=float, default=1.5, help="切换状态后的等待秒数")
    probe_cmd.add_argument("--commit", action="store_true", help="真的写入（默认仅演练）")
    probe_cmd.add_argument("--readback-delay", type=float, default=1.5,
                           help="写入后等待多少秒再回读确认")
    probe_cmd.add_argument("--mutate", action="store_true",
                           help="写「不同的合法值」而不是写回原值（能测出状态依赖）")
    probe_cmd.add_argument("--allow-transition", action="store_true",
                           help="允许切换设备开关以对比状态（默认禁止：只在当前状态探测）")
    probe_cmd.add_argument("--out", default=None, help="可用性规则输出路径")
    probe_cmd.set_defaults(func=cmd_probe)
    spec_cmd = sub.add_parser("spec", help="取回并解析设备规格（did 或 urn）")
    spec_cmd.add_argument("target")
    spec_cmd.set_defaults(func=cmd_spec)

    plugin_cmd = sub.add_parser(
        "plugin-sync", help="同步设备插件包并解析其中的中文文案")
    plugin_cmd.add_argument("models", nargs="*",
                            help="型号（省略则用账号里全部设备）")
    plugin_cmd.add_argument("--force", action="store_true",
                            help="忽略缓存重新下载")
    plugin_cmd.set_defaults(func=_cmd_plugin_sync)

    serve = sub.add_parser("serve", help="启动本地网页界面")
    serve.add_argument("--port", type=int, default=0, help="监听端口（0=自动）")
    serve.add_argument("--no-browser", action="store_true", help="不自动打开窗口")
    serve.set_defaults(func=cmd_serve)
    app_cmd = sub.add_parser("app", help="桌面模式：托盘图标 + 本地界面")
    app_cmd.add_argument("--port", type=int, default=0)
    app_cmd.add_argument("--no-browser", action="store_true")
    app_cmd.set_defaults(func=cmd_app)
    selftest = sub.add_parser("selftest", help="离线自检")
    selftest.add_argument("--env", action="store_true",
                          help="同时打印数据目录/凭据/插件文案的状态")
    selftest.set_defaults(func=cmd_selftest)
    return parser


def _cmd_plugin_sync(args):
    """下载设备插件包并解析其中的中文文案（无插件的型号如实标注）。"""
    from .xiaomi import plugin as plugin_mod

    _creds, client = _load_session()
    models = list(getattr(args, "models", []) or [])
    if not models:
        models = sorted({d.get("model") for d in _extract_devices(client.devices())
                         if d.get("model")})
    root = os.path.join(data_dir(), "plugins")
    for row in plugin_mod.sync_all(client, models, root, force=args.force):
        if row.get("error"):
            print("  %-28s ERROR %s" % (row["model"], row["error"]))
        elif row.get("no_url"):
            print("  %-28s plugin=%-6s \u670d\u52a1\u7aef\u4e0d\u7ed9\u5305" % (
                row["model"], row["plugin_id"]))
        elif row.get("plugin_id"):
            print("  %-28s plugin=%-6s \u6587\u6848 %4s \u6761 / \u5177\u540d %4s \u6761 %s" % (
                row["model"], row["plugin_id"], row.get("strings") or 0,
                row.get("texts") or 0,
                "[\u7f13\u5b58]" if row.get("cached") else "[\u5df2\u4e0b\u8f7d]"))
        else:
            print("  %-28s 无插件" % row["model"])
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args) or 0
