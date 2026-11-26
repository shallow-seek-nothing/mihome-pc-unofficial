"""Build the Linux deliverables without any Debian tooling.

Produces, under ``dist/``:

* ``mihome-pc_<ver>_all.deb``  -- a real ``ar`` archive holding
  ``debian-binary`` + ``control.tar.gz`` + ``data.tar.gz``
* ``mihome-pc-<ver>-linux.tar.gz`` -- portable tarball for non-Debian distros
  (Fedora, RHEL, Arch, Alpine, openSUSE ...) together with ``install.sh``

Why hand-roll *ar*: ``dpkg-deb`` does not exist on the build machine and the
format is small and stable.  Writing it ourselves keeps the build reproducible
and cross-platform -- the target is "any mainstream distribution", so the build
must not depend on the build host being Debian.

Layout inside the package::

    /usr/lib/mihome-pc/mihome_pc/...    the package (pure Python, no deps)
    /usr/bin/mihome-pc                  launcher (calls python3 -m mihome_pc)
    /usr/share/applications/mihome-pc.desktop
    /usr/share/doc/mihome-pc/{copyright,README.md}
    /usr/lib/systemd/user/mihome-pc.service   (optional, user unit)

Usage: python packaging/build_deb.py
"""
import os
import shutil
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
STAGE = os.path.join(DIST, "_pkg")



def _read_version():
    """Version from ``mihome_pc/__init__.py`` (single source of truth)."""
    path = os.path.join(ROOT, "mihome_pc", "__init__.py")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("VERSION") and "=" in line:
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("cannot read VERSION from %s" % path)

# Version comes from the package itself, so the UI and the build agree.
VERSION = _read_version()
PACKAGE = "mihome-pc"
ARCH = "all"
MAINTAINER = "the mihome-pc authors <shallow-seek-nothing@users.noreply.github.com>"
HOMEPAGE = "https://github.com/shallow-seek-nothing/mihome-pc-unofficial"
FIXED_MTIME = 1700000000            # reproducible builds: no wall-clock in tars
ICON_SIZES = (48, 128, 256)
ICON_SOURCE = os.path.join(ROOT, "mihome_pc", "assets", "icons")

LAUNCHER = """#!/bin/sh
# Mi Home PC client launcher.
#
# Uses the system python3 (>= 3.8) because the package is pure standard-library
# Python: nothing to compile, so one build works on every distribution.
set -e
PREFIX="${MIHOME_PC_PREFIX:-/usr/lib/mihome-pc}"
PYTHON="${MIHOME_PC_PYTHON:-python3}"

if [ ! -d "$PREFIX/mihome_pc" ]; then
    echo "mihome-pc: package data missing at $PREFIX" >&2
    exit 1
fi

PYTHONPATH="$PREFIX${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

if [ "$#" -eq 0 ]; then
    set -- serve
fi
exec "$PYTHON" -m mihome_pc "$@"
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=Mi Home
Name[zh_CN]=\u7c73\u5bb6
Comment=Control Mi Home devices from your PC
Comment[zh_CN]=\u5728\u7535\u8111\u4e0a\u63a7\u5236\u7c73\u5bb6\u8bbe\u5907
# `app` is the desktop entry point (re-uses a running instance); `serve` would
# leave a headless process with no way to quit on Linux.
Exec=mihome-pc app
TryExec=mihome-pc
Icon=mihome-pc
Terminal=false
Categories=Utility;Network;
Keywords=mihome;mi home;xiaomi;smarthome;
StartupWMClass=MiHome
"""

SYSTEMD_UNIT = """[Unit]
Description=Mi Home PC client (local web UI)
Documentation=%s
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/mihome-pc serve --no-browser --port 0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""" % HOMEPAGE

COPYRIGHT = """Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: mihome-pc

Files: *
Copyright: the mihome-pc authors
License: MIT

Reverse engineering notes (protocol facts, not code) were derived from the
public Mi Home APK for interoperability; no APK code or assets that are
copyrighted by Xiaomi are redistributed in this package. Device names and
specification data are cached at runtime from public endpoints.
"""

INSTALL_SH = """#!/bin/sh
# Portable installer for non-Debian distributions.
#
#   sh install.sh [--prefix /usr/local]
#
# Installs the Python package to <prefix>/lib/mihome-pc and a launcher to
# <prefix>/bin/mihome-pc.  No compilation, no Python dependencies beyond 3.8.
set -e
PREFIX=/usr
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --prefix=*) PREFIX="${1#*=}"; shift ;;
        -h|--help) echo "usage: sh install.sh [--prefix DIR]"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
LIBDIR="$PREFIX/lib/mihome-pc"
BINDIR="$PREFIX/bin"
SHAREDIR="$PREFIX/share"
APPDIR="$SHAREDIR/applications"

if [ "$(id -u)" -ne 0 ] && [ ! -w "$PREFIX" ]; then
    echo "need root (or a writable prefix) to install into $PREFIX" >&2
    exit 1
fi

echo "installing to $LIBDIR"
mkdir -p "$LIBDIR" "$BINDIR" "$APPDIR"
rm -rf "$LIBDIR/mihome_pc"
cp -R "$HERE/lib/mihome_pc" "$LIBDIR/"

cat > "$BINDIR/mihome-pc" <<LAUNCH
#!/bin/sh
set -e
PYTHONPATH="$LIBDIR\\${PYTHONPATH:+:\\$PYTHONPATH}"
export PYTHONPATH
if [ "\\$#" -eq 0 ]; then set -- app; fi
exec \\${MIHOME_PC_PYTHON:-python3} -m mihome_pc "\\$@"
LAUNCH
chmod 755 "$BINDIR/mihome-pc"

# Desktop integration: menu entry + PNG icons (shipped inside the tarball;
# without them the tarball user gets a command-line-only install).
if [ -f "$HERE/share/applications/mihome-pc.desktop" ]; then
    cp "$HERE/share/applications/mihome-pc.desktop" "$APPDIR/mihome-pc.desktop"
    chmod 644 "$APPDIR/mihome-pc.desktop"
fi
for size in 48 128 256; do
    src="$HERE/share/icons/hicolor/${size}x${size}/apps/mihome-pc.png"
    if [ -f "$src" ]; then
        dst="$SHAREDIR/icons/hicolor/${size}x${size}/apps"
        mkdir -p "$dst"
        cp "$src" "$dst/mihome-pc.png"
    fi
done
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$SHAREDIR/icons/hicolor" 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPDIR" 2>/dev/null || true
fi

echo "installed. run: mihome-pc app     (or: mihome-pc serve / mihome-pc stop)"
echo "python requirement: 3.8 or newer (found: $(${MIHOME_PC_PYTHON:-python3} -V 2>&1))"
"""


def log(message):
    print(message)


# --------------------------------------------------------------------------
# ar archive
# --------------------------------------------------------------------------

def _ar_member_header(name, size, mtime, mode=0o100644):
    header = f"{name:<16}{int(mtime):<12}{0:<6}{0:<6}{mode:<8o}{size:<10}`\n"
    data = header.encode("ascii")
    assert len(data) == 60, len(data)
    return data


def write_ar(path, members):
    """``members`` = [(name, bytes)] in order."""
    with open(path, "wb") as handle:
        handle.write(b"!<arch>\n")
        for name, payload in members:
            handle.write(_ar_member_header(name, len(payload), time.time()))
            handle.write(payload)
            if len(payload) % 2:
                handle.write(b"\n")


def read_ar(path):
    """Minimal reader used by the verifier; returns [(name, bytes)]."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if not blob.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive")
    offset = 8
    members = []
    while offset + 60 <= len(blob):
        header = blob[offset:offset + 60]
        name = header[0:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        start = offset + 60
        members.append((name, blob[start:start + size]))
        offset = start + size + (size % 2)
    return members


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------

def build_tar_gz(path, root, owner="root", executables=(), prefix="./"):
    """Deterministic tar.gz of ``root`` -- **including directory entries**.

    dpkg does not create missing parent directories while unpacking, so a
    ``data.tar.gz`` that lists only files dies with "No such file or directory"
    on the first nested path.  That was the 0.7.38 packaging regression (63
    files, 0 directories).  Modes are normalized here as well: inheriting the
    build host's makes Windows produce 0666 files, which dpkg then installs
    world-writable.
    """
    dirs = ["."]
    files = []
    for base, subdirs, names in os.walk(root):
        subdirs.sort()
        rel_dir = os.path.relpath(base, root).replace("\\", "/")
        if rel_dir != ".":
            dirs.append(rel_dir)
        for name in sorted(names):
            full = os.path.join(base, name)
            files.append((os.path.relpath(full, root).replace("\\", "/"), full))
    execs = set(executables)
    with tarfile.open(path, "w:gz", compresslevel=9) as archive:
        for rel in sorted(dirs, key=lambda item: (item.count("/"), item)):
            name = prefix if rel == "." else prefix + rel + "/"
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = 0
            info.gid = 0
            info.uname = owner
            info.gname = owner
            info.mtime = FIXED_MTIME
            archive.addfile(info)
        for rel, full in sorted(files):
            info = archive.gettarinfo(full, arcname=prefix + rel)
            info.uid = 0
            info.gid = 0
            info.uname = owner
            info.gname = owner
            info.mtime = FIXED_MTIME
            info.mode = 0o755 if rel in execs else 0o644
            with open(full, "rb") as handle:
                archive.addfile(info, handle)
    return path


def stage_tree():
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)

    # package payload
    lib_dir = os.path.join(STAGE, "usr", "lib", PACKAGE)
    shutil.copytree(os.path.join(ROOT, "mihome_pc"),
                    os.path.join(lib_dir, "mihome_pc"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # launcher
    bin_dir = os.path.join(STAGE, "usr", "bin")
    os.makedirs(bin_dir)
    with open(os.path.join(bin_dir, PACKAGE), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write(LAUNCHER)

    # desktop entry
    apps_dir = os.path.join(STAGE, "usr", "share", "applications")
    os.makedirs(apps_dir)
    with open(os.path.join(apps_dir, PACKAGE + ".desktop"), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write(DESKTOP)

    # docs
    doc_dir = os.path.join(STAGE, "usr", "share", "doc", PACKAGE)
    os.makedirs(doc_dir)
    with open(os.path.join(doc_dir, "copyright"), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write(COPYRIGHT)
    readme = os.path.join(ROOT, "README.md")
    if os.path.exists(readme):
        shutil.copyfile(readme, os.path.join(doc_dir, "README.md"))

    # icons: the .desktop entry references Icon=mihome-pc, so the PNGs must be
    # installed under hicolor/ or the menu shows a blank square
    for size in ICON_SIZES:
        source = os.path.join(ICON_SOURCE, "mihome-%d.png" % size)
        if not os.path.isfile(source):
            raise SystemExit(
                "missing icon %s -- run: python packaging/make_icons.py" % source)
        icon_dir = os.path.join(STAGE, "usr", "share", "icons", "hicolor",
                                "%dx%d" % (size, size), "apps")
        if not os.path.isdir(icon_dir):
            os.makedirs(icon_dir)
        shutil.copyfile(source, os.path.join(icon_dir, PACKAGE + ".png"))

    # systemd user unit
    unit_dir = os.path.join(STAGE, "usr", "lib", "systemd", "user")
    os.makedirs(unit_dir)
    with open(os.path.join(unit_dir, PACKAGE + ".service"), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write(SYSTEMD_UNIT)
    return STAGE


def build_control_tar(path, installed_size):
    control = f"""Package: {PACKAGE}
Version: {VERSION}
Section: utils
Priority: optional
Architecture: {ARCH}
Depends: python3 (>= 3.8)
Recommends: chromium | firefox | xdg-utils
Installed-Size: {installed_size}
Maintainer: {MAINTAINER}
Homepage: {HOMEPAGE}
Description: Mi Home client for the desktop (local web UI)
 A personal-computer client for Mi Home devices, reimplemented from the
 reverse-engineered protocol rather than reusing the Android app.
 .
 Pure Python 3.8+ standard library, no third-party dependencies. The UI is a
 local web page; it opens in a Chromium --app window when one is available and
 falls back to the default browser otherwise.
"""
    tmp = path + ".control"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(control)
    with tarfile.open(path, "w:gz", compresslevel=9) as archive:
        info = archive.gettarinfo(tmp, arcname="./control")
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mode = 0o644
        info.mtime = 1700000000
        with open(tmp, "rb") as handle:
            archive.addfile(info, handle)
    os.remove(tmp)
    return path


def _installed_size(root):
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            total += os.path.getsize(os.path.join(base, name))
    return max(1, total // 1024)


def build_deb():
    stage = stage_tree()
    if not os.path.isdir(DIST):
        os.makedirs(DIST)
    data_tar = os.path.join(DIST, "_data.tar.gz")
    control_tar = os.path.join(DIST, "_control.tar.gz")
    build_tar_gz(data_tar, stage, executables=("usr/bin/" + PACKAGE,))
    build_control_tar(control_tar, _installed_size(stage))

    deb_path = os.path.join(DIST, "%s_%s_%s.deb" % (PACKAGE, VERSION, ARCH))
    with open(control_tar, "rb") as control_handle:
        control_blob = control_handle.read()
    with open(data_tar, "rb") as data_handle:
        data_blob = data_handle.read()
    write_ar(deb_path, [
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control_blob),
        ("data.tar.gz", data_blob),
    ])
    os.remove(data_tar)
    os.remove(control_tar)
    return deb_path


def build_portable_tarball():
    """Tarball + install.sh for Fedora/RHEL/Arch/Alpine/openSUSE.

    Carries the same desktop integration as the deb (``.desktop`` + PNG icons),
    otherwise a tarball user ends up with a command-line-only install and no
    menu entry.
    """
    portable = os.path.join(DIST, "_portable")
    if os.path.isdir(portable):
        shutil.rmtree(portable)
    os.makedirs(portable)
    shutil.copytree(os.path.join(ROOT, "mihome_pc"),
                    os.path.join(portable, "lib", "mihome_pc"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(portable, "install.sh"), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write(INSTALL_SH)
    with open(os.path.join(portable, "README.md"), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write("# Mi Home PC %s (Linux tarball)\n\n"
                     "    sh install.sh              # into /usr\n"
                     "    sh install.sh --prefix ~/.local\n\n"
                     "Requires python3 >= 3.8. No other dependencies.\n"
                     "Starts in the background; `mihome-pc stop` quits it.\n"
                     % VERSION)

    apps_dir = os.path.join(portable, "share", "applications")
    os.makedirs(apps_dir)
    with open(os.path.join(apps_dir, PACKAGE + ".desktop"), "w",
                 encoding="utf-8", newline="\n") as handle:
        handle.write(DESKTOP)
    for size in ICON_SIZES:
        source = os.path.join(ICON_SOURCE, "mihome-%d.png" % size)
        if not os.path.isfile(source):
            raise SystemExit(
                "missing icon %s -- run: python packaging/make_icons.py" % source)
        icon_dir = os.path.join(portable, "share", "icons", "hicolor",
                                "%dx%d" % (size, size), "apps")
        if not os.path.isdir(icon_dir):
            os.makedirs(icon_dir)
        shutil.copyfile(source, os.path.join(icon_dir, PACKAGE + ".png"))

    tar_path = os.path.join(DIST, "%s-%s-linux.tar.gz" % (PACKAGE, VERSION))
    build_tar_gz(tar_path, portable, executables=("install.sh",), prefix="")
    shutil.rmtree(portable)
    return tar_path


def main():
    if not os.path.isdir(DIST):
        os.makedirs(DIST)
    deb = build_deb()
    tar = build_portable_tarball()
    shutil.rmtree(STAGE, ignore_errors=True)

    for path in (deb, tar):
        log("%-52s %8.1f KB" % (os.path.basename(path),
                                os.path.getsize(path) / 1024.0))
    log("")
    log("verify with: python packaging/verify_deb.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
