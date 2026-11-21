"""Desktop entry point: local server + tray icon + single instance.

``python -m mihome_pc app`` is what the packaged launchers call.  It differs
from ``serve`` in three ways that only matter on a desktop:

* the window/tray icon owns the process lifetime (right-click -> \u9000\u51fa),
* a second launch re-uses the running instance instead of binding a new port
  (two servers on one port silently split requests -- that happened during
  development and cost an hour),
* the data directory gets ``app.lock`` with the URL so both of the above work.
"""
import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

__all__ = ["run", "read_lock", "running_instance"]

LOCK_NAME = "app.lock"


def _say(text):
    """print() that survives ``pythonw`` (where ``sys.stdout`` is None)."""
    with contextlib.suppress(Exception):
        print(text)


def _data_dir():
    from .cli import data_dir
    return data_dir()


def _lock_path():
    return os.path.join(_data_dir(), LOCK_NAME)


def read_lock():
    """``{"pid": int, "url": str}`` from a previous run, or ``{}``."""
    try:
        with open(_lock_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_lock(url):
    path = _lock_path()
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "url": url, "since": int(time.time())},
                  handle)


def _drop_lock():
    with contextlib.suppress(OSError):
        os.remove(_lock_path())


def responds(url, timeout=0.6):
    """True when ``url`` hosts a live instance of this app."""
    if not url:
        return False
    try:
        probe = url.rstrip("/") + "/api/status"
        with urllib.request.urlopen(probe, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def running_instance():
    """URL of an already-running instance, or ``""``."""
    info = read_lock()
    url = info.get("url") or ""
    return url if responds(url) else ""


def _tray_ready_file():
    """Marker the tray shim writes once the icon is registered (diagnostics)."""
    return os.path.join(_data_dir(), "logs", "tray.ready")


def _icon_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("mihome.ico", "favicon.ico"):
        path = os.path.join(here, "assets", name)
        if os.path.isfile(path):
            return path
    static = os.path.join(here, "static", "favicon.png")
    return static if os.path.isfile(static) else ""


def run(port=0, open_browser=True, use_tray=None):
    """Start the desktop app; returns a process exit code."""
    from .web import server as web_server
    from .web import launcher
    from . import tray as tray_mod

    existing = running_instance()
    if existing:
        _say("\u5df2\u7ecf\u5728\u8fd0\u884c: %s" % existing)
        if open_browser:
            launcher.open_ui(existing)
        return 0

    server = web_server.create_server(provider=None, port=port)
    # create_server(provider=None) follows credentials.json, so a QR login
    # takes effect without restarting the process.
    provider = server.RequestHandlerClass.provider_state["provider"]
    if not provider.demo:
        web_server.sync_plugins_in_background(provider)
    host, bound = server.server_address[0], server.server_address[1]
    url = "http://%s:%d/" % (host, bound)
    _write_lock(url)

    thread = threading.Thread(target=server.serve_forever, name="http-server")
    thread.daemon = True
    thread.start()

    _say("mihome-pc \u5df2\u542f\u52a8: %s" % url)
    _say("\u6258\u76d8\u56fe\u6807\uff1a\u5de6\u952e\u6253\u5f00\u754c\u9762\uff0c\u53f3\u952e\u9000\u51fa")
    if open_browser:
        launcher.open_ui(url)

    if use_tray is None:
        use_tray = tray_mod.available()
    if not use_tray:
        _run_headless(url)
        server.shutdown()
        server.server_close()
        _drop_lock()
        return 0

    from . import VERSION, tray_ps
    try:
        # PowerShell 那条先试：本机实测 ctypes 的 Shell_NotifyIcon 会间歇性
        # 返回 ERROR_ACCESS_DENIED（同一台机器，换个启动方式就正常），而
        # .NET 的 NotifyIcon 稳定可用。ctypes 版本保留给没有 PowerShell 的环境。
        if tray_ps.available():
            _run_powershell_tray(url, VERSION)
        elif tray_mod.probe(_icon_path()):
            tray_mod.Tray(
                title="\u7c73\u5bb6 PC", icon_path=_icon_path(), url=url,
                version=VERSION, open_url=launcher.open_ui,
                open_folder=lambda: launcher.open_path(_data_dir())).run()
        else:
            _run_headless(url)
    finally:
        server.shutdown()
        server.server_close()
        _drop_lock()
    return 0


def _run_headless(url):
    """No tray available (Linux, or a locked-down Windows session)."""
    _say("\u6ca1\u6709\u6258\u76d8\u65b9\u6848\uff0c\u754c\u9762\u4ecd\u5728 %s" % url)
    _say("\u5173\u95ed\u7a97\u53e3\u6216 Ctrl+C \u9000\u51fa")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _run_powershell_tray(url, version):
    """Fallback used when ctypes cannot register a tray icon (see tray_ps)."""
    from . import tray_ps
    ready = _tray_ready_file()
    with contextlib.suppress(OSError):
        os.remove(ready)               # 上一次被强杀时留下的旧标记
    folder = os.path.dirname(ready)
    if folder and not os.path.isdir(folder):
        with contextlib.suppress(OSError):
            os.makedirs(folder)
    if not tray_ps.available():
        _run_headless(url)
        return
    tray_ps.run(url=url, app_pid=os.getpid(), icon=_icon_path(),
                title="\u7c73\u5bb6 PC", folder=_data_dir(),
                ready_file=ready)
