"""Open the local UI in something that looks like a native window.

On Windows 7 the newest Chromium is 109, which still supports ``--app=<url>``.
That gives a chrome-less window without shipping WebView2 or a runtime: we just
reuse whatever browser the user already has.  If nothing Chromium-ish is found
we fall back to the platform's default browser -- the UI is identical, only the
window furniture differs.
"""
import os
import shutil
import subprocess
import sys

__all__ = ["find_chromium", "open_path", "open_ui"]

_WINDOWS_CANDIDATES = [
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
]

_POSIX_CANDIDATES = [
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
    "brave-browser", "microsoft-edge", "microsoft-edge-stable", "vivaldi",
]


def find_chromium():
    """Absolute path to a Chromium-family browser, or ``None``."""
    if sys.platform.startswith("win"):
        for template in _WINDOWS_CANDIDATES:
            path = os.path.expandvars(template)
            if os.path.isfile(path):
                return path
        for name in ("msedge.exe", "chrome.exe", "brave.exe"):
            found = shutil.which(name)
            if found:
                return found
        return None
    for name in _POSIX_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def open_path(path):
    """Open a folder/file in the platform file manager (best effort)."""
    if not path:
        return ""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                 # noqa: S606 - stdlib API
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return path
    except OSError:
        return ""


def open_ui(url, prefer_app=True):
    """Open ``url``; returns a short description of what was used."""
    browser = find_chromium() if prefer_app else None
    if browser:
        args = [browser, "--app=" + url, "--window-size=1180,800"]
        if sys.platform.startswith("win"):
            args.insert(1, "--no-first-run")
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return "app window (%s)" % os.path.basename(browser)
        except OSError:
            pass
    try:
        import webbrowser
        if webbrowser.open(url):
            return "default browser"
    except Exception:                              # noqa: BLE001
        pass
    return ""
