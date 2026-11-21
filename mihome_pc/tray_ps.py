"""PowerShell shim for the notification-area icon.

ctypes + Shell_NotifyIcon is the nicer implementation (see ``tray.py``), but
in some sessions Windows answers ERROR_ACCESS_DENIED to it while .NET's
NotifyIcon works fine.  Since the requirement is "there must be an icon with a
right-click \u9000\u51fa", the app probes the ctypes path and falls back to this
shim, which is plain PowerShell 2.0 and therefore fine on Windows 7.
"""
import os
import subprocess
import sys

__all__ = ["available", "run"]


def _script_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "tray.ps1")


def _powershell():
    for name in ("powershell.exe", "pwsh.exe"):
        found = _which(name)
        if found:
            return found
    return ""


def _which(name):
    from shutil import which
    return which(name) or ""


def available():
    """True when the shim can be used on this machine."""
    return sys.platform.startswith("win") and os.path.isfile(_script_path()) \
        and bool(_powershell())


def run(url, app_pid, icon="", title="\u7c73\u5bb6 PC", folder="",
        ready_file=""):
    """Show the icon and block until the user picks \u9000\u51fa (or we die).

    Returns the PowerShell exit code; the caller treats any return as
    "the user closed us", which is exactly right.
    """
    shell = _powershell()
    if not shell:
        raise OSError("PowerShell not found")
    command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", _script_path(),
               "-Url", url, "-AppPid", str(app_pid), "-Title", title]
    if icon:
        command += ["-Icon", icon]
    if folder:
        command += ["-Folder", folder]
    if ready_file:
        command += ["-ReadyFile", ready_file]
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    process = subprocess.Popen(command, creationflags=flags,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return 1
