"""Windows tray icon for the app (stdlib only, via ctypes).

Why hand-rolled: the product ships a bare CPython with no third-party
packages, so ``pystray``/``PyQt`` are not options.  Shell_NotifyIconW plus a
hidden window and a popup menu is all that is needed -- about 200 lines.

When there is no tray (Linux, or Windows without a shell) :func:`available`
returns False and the caller falls back to a plain foreground server.
"""
import ctypes
import os
import struct
import sys

__all__ = ["available", "build_menu", "ico_from_png", "probe", "Tray"]

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_NULL = 0x0000
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0, 0x800, 0x1
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_NONOTIFY = 0x100, 0x2, 0x80

ID_OPEN, ID_FOLDER, ID_QUIT = 1001, 1002, 1003


def available():
    """True when this platform can show a notification-area icon."""
    return sys.platform.startswith("win")


def build_menu(url, version=""):
    """Popup menu entries as ``[(id, label, enabled)]`` (pure, testable)."""
    items = [(ID_OPEN, "\u6253\u5f00\u7c73\u5bb6 PC", True)]
    if url:
        items.append((0, url, False))
    if version:
        items.append((0, "\u7248\u672c " + version, False))
    items.append((-1, "", False))
    items.append((ID_FOLDER, "\u6253\u5f00\u6570\u636e\u76ee\u5f55", True))
    items.append((ID_QUIT, "\u9000\u51fa", True))
    return items


def ico_from_png(png_bytes, size=None):
    """Wrap a PNG in an ICO container (Vista+ accepts PNG-compressed icons).

    Used at build time to turn ``static/logo.png`` into ``mihome.ico`` without
    pulling in Pillow.  The stored width/height come from the PNG's IHDR
    (0 means 256, per the ICO spec); ``size`` overrides them.
    """
    width = size
    if width is None:
        width = 0
        if png_bytes[:8] == b"\x89PNG\r\n\x1a\n" and len(png_bytes) >= 24:
            raw_width = struct.unpack(">I", png_bytes[16:20])[0]
            width = 0 if raw_width >= 256 else raw_width
    header = struct.pack("<HHH", 0, 1, 1)          # reserved, type=icon, count
    entry = struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32,
                        len(png_bytes), 22)
    return header + entry + png_bytes


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_CLASS_COUNTER = [0]


def _next_class_name():
    """A fresh window-class name per instance.

    ``probe()`` may register the class first (to find out whether this session
    allows a tray icon at all), and Windows refuses to register the same class
    twice in one process -- error 1410, which used to kill the app right after
    a successful probe.
    """
    _CLASS_COUNTER[0] += 1
    return "MihomePcTray%d" % _CLASS_COUNTER[0]


def _win_libs():
    """User32/Kernel32/Shell32 with the prototypes that actually matter.

    Without these, ctypes returns ``int`` for pointer-returning calls, so the
    hInstance gets truncated on 64-bit and CreateWindowEx fails with 1401.
    """
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                  ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_uint]
    user32.CreatePopupMenu.restype = ctypes.c_void_p
    user32.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                   ctypes.c_size_t, ctypes.c_wchar_p]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_void_p]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG),
                                    ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_uint]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DestroyMenu.argtypes = [ctypes.c_void_p]
    user32.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                    ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    shell32.Shell_NotifyIconW.argtypes = [ctypes.c_uint,
                                          ctypes.POINTER(_NOTIFYICONDATAW)]
    return user32, kernel32, shell32


class Tray:
    """A notification-area icon with a right-click menu.

    The menu model is :func:`build_menu`; this class only wires it to Win32.
    ``open_url`` is called for the \u6253\u5f00 entry / double click, ``open_folder``
    for \u6570\u636e\u76ee\u5f55 and the loop returns when \u9000\u51fa is chosen (or
    :meth:`quit` is posted from another thread).
    """

    def __init__(self, title, icon_path, url="", version="",
                 open_url=None, open_folder=None):
        if not available():
            raise OSError("no tray support on this platform")
        self.title = title
        self.icon_path = icon_path
        self.url = url
        self.version = version
        self.open_url = open_url
        self.open_folder = open_folder
        self._user32, self._kernel32, self._shell32 = _win_libs()
        self._hwnd = None
        self._class_name = _next_class_name()
        self._proc = None
        self._icon_data = _NOTIFYICONDATAW()
        self._hicon = None

    # -- win32 plumbing ------------------------------------------------
    def _create_window(self):
        from ctypes import wintypes
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                                     wintypes.UINT, ctypes.c_size_t,
                                     ctypes.c_ssize_t)
        self._proc = WNDPROC(self._on_message)
        hinst = self._kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        if not self._user32.RegisterClassExW(ctypes.byref(wc)):
            error = ctypes.get_last_error()
            if error != 1410:              # 1410 = class already exists
                raise OSError("RegisterClassEx failed: %s" % error)
        hwnd = self._user32.CreateWindowExW(
            0, self._class_name, self._class_name, 0, 0, 0, 0, 0,
            None, None, hinst, None)
        if not hwnd:
            raise OSError("CreateWindowEx failed: %s" % ctypes.get_last_error())
        self._hwnd = hwnd

    def _add_icon(self):
        if self.icon_path and os.path.isfile(self.icon_path):
            self._hicon = self._user32.LoadImageW(
                None, self.icon_path, IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE)
        data = self._icon_data
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._hicon or 0
        tip = self.title[:127]
        data.szTip = tip
        if not self._shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError("Shell_NotifyIcon add failed: %s"
                          % ctypes.get_last_error())

    def _show_menu(self):
        menu = self._user32.CreatePopupMenu()
        for item_id, label, enabled in build_menu(self.url, self.version):
            if item_id == -1:
                self._user32.AppendMenuW(menu, MF_SEPARATOR, 0, "")
                continue
            flags = MF_STRING | (0 if enabled else MF_GRAYED)
            self._user32.AppendMenuW(menu, flags, item_id, label)
        point = _POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        self._user32.SetForegroundWindow(self._hwnd)
        chosen = self._user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
            point.x, point.y, 0, self._hwnd, None)
        self._user32.DestroyMenu(menu)
        self._user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        return chosen

    def _fire(self, item_id):
        if item_id == ID_OPEN and self.open_url:
            self.open_url(self.url)
        elif item_id == ID_FOLDER and self.open_folder:
            self.open_folder()
        elif item_id == ID_QUIT:
            self._user32.PostMessageW(self._hwnd, WM_QUIT, 0, 0)

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_RBUTTONUP:
                self._fire(self._show_menu())
            elif event == WM_LBUTTONDBLCLK:
                self._fire(ID_OPEN)
            return 0
        if message == WM_DESTROY:
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    # -- public ---------------------------------------------------------
    def run(self):
        """Create the icon and pump messages until \u9000\u51fa."""
        from ctypes import wintypes
        self._create_window()
        self._add_icon()
        message = wintypes.MSG()
        try:
            while True:
                result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break                      # WM_QUIT
                if result == -1:
                    raise OSError("GetMessageW failed: %s"
                                  % ctypes.get_last_error())
                self._user32.TranslateMessage(ctypes.byref(message))
                self._user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._shell32.Shell_NotifyIconW(NIM_DELETE,
                                            ctypes.byref(self._icon_data))
            if self._hwnd:
                self._user32.DestroyWindow(self._hwnd)
                self._hwnd = None
        return 0

    def _remove_icon(self):
        if self._icon_data.cbSize:
            self._shell32.Shell_NotifyIconW(NIM_DELETE,
                                            ctypes.byref(self._icon_data))
            self._icon_data.cbSize = 0

    def _destroy(self):
        if self._hwnd:
            self._user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    def quit(self):
        """Ask the loop to stop (safe from another thread)."""
        if self._hwnd:
            self._user32.PostMessageW(self._hwnd, WM_QUIT, 0, 0)


def probe(icon_path=""):
    """True when this environment lets us add a notification-area icon.

    Some sessions refuse ``Shell_NotifyIcon`` with ERROR_ACCESS_DENIED even
    though the same user can show one from .NET -- so the caller keeps a
    PowerShell fallback (``mihome_pc/tray_ps.py``) and asks this first.
    """
    if not available():
        return False
    icon = Tray("probe", icon_path)
    try:
        icon._create_window()
        icon._add_icon()
        return True
    except OSError:
        return False
    finally:
        icon._remove_icon()
        icon._destroy()
