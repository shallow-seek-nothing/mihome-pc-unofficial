"""Verify the generated .deb / tarball without dpkg.

Checks that matter for "installable on the target distros":

* the ar container parses and its members are in the required order
* ``debian-binary`` says ``2.0``
* ``control.tar.gz`` has ``./control`` with the mandatory fields
* ``data.tar.gz`` contains the launcher (mode 0755), the Python package, the
  desktop entry and the systemd unit
* every shipped ``.py`` file parses under the *running* interpreter's grammar
  and contains no ``__pycache__``/``.pyc`` noise
* the tarball carries ``install.sh`` and the same package tree

Usage: python packaging/verify_deb.py
"""
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_deb import PACKAGE, VERSION, read_ar  # noqa: E402

DIST = os.path.join(ROOT, "dist")
DEB = os.path.join(DIST, "%s_%s_all.deb" % (PACKAGE, VERSION))
TARBALL = os.path.join(DIST, "%s-%s-linux.tar.gz" % (PACKAGE, VERSION))

REQUIRED_FIELDS = ("Package", "Version", "Architecture", "Depends", "Maintainer",
                   "Description")

failures = []


def check(condition, message):
    if condition:
        print("  ok    %s" % message)
    else:
        print("  FAIL  %s" % message)
        failures.append(message)


def read_tar_members(blob):
    """Return {name: (mode, size)} for a gzipped tarball given as bytes."""
    import gzip
    import tarfile
    from io import BytesIO
    members = {}
    with tarfile.open(fileobj=BytesIO(gzip.decompress(blob))) as archive:
        for member in archive.getmembers():
            name = member.name
            if name.startswith("./"):
                name = name[2:]
            members[name] = (member.mode, member.size, member.isfile())
    return members


def read_member(blob, wanted):
    """Text of one file inside a gzipped tarball, or ``""`` when absent."""
    import gzip
    import tarfile
    from io import BytesIO
    with tarfile.open(fileobj=BytesIO(gzip.decompress(blob))) as archive:
        for member in archive.getmembers():
            name = member.name
            if name.startswith("./"):
                name = name[2:]
            if name.rstrip("/") == wanted.rstrip("/") and member.isfile():
                payload = archive.extractfile(member)
                return payload.read().decode("utf-8", "replace") if payload else ""
    return ""


def main():
    print("deb: %s" % DEB)
    if not os.path.exists(DEB):
        raise SystemExit("missing %s -- run packaging/build_deb.py first" % DEB)

    members = read_ar(DEB)
    names = [name for name, _ in members]
    check(names == ["debian-binary", "control.tar.gz", "data.tar.gz"],
          "ar members are exactly debian-binary, control.tar.gz, data.tar.gz (%s)"
          % ", ".join(names))

    payload = dict(members)
    check(payload.get("debian-binary") == b"2.0\n", "debian-binary == '2.0\\n'")

    control_members = read_tar_members(payload["control.tar.gz"])
    check("control" in control_members, "control.tar.gz contains ./control")
    control_text = None
    if "control" in control_members:
        import gzip
        from io import BytesIO
        with tarfile.open(fileobj=BytesIO(gzip.decompress(payload["control.tar.gz"]))) as archive:
            entry = next(m for m in archive.getmembers()
                         if m.name.lstrip("./") == "control")
            handle = archive.extractfile(entry)
            control_text = handle.read().decode("utf-8")
    if control_text:
        for field in REQUIRED_FIELDS:
            check(("%s:" % field) in control_text, "control has %s" % field)
        check("python3 (>= 3.8)" in control_text, "depends on python3 >= 3.8")
        check("Architecture: all" in control_text, "architecture is 'all'")
        check("example.invalid" not in control_text,
              "Maintainer is not the placeholder address")
        check("Homepage: https://github.com/shallow-seek-nothing" in control_text,
              "Homepage points at the real repository")

    data_members = read_tar_members(payload["data.tar.gz"])
    keys = sorted(data_members)
    print("  data.tar.gz: %d entries" % len(keys))

    # 0.7.38 shipped 63 files / 0 directory entries -> dpkg could not create
    # /usr/lib/mihome-pc/mihome_pc/ and every install failed.
    dir_entries = [key for key in keys if not data_members[key][2]]
    check(len(dir_entries) > 0,
          "data.tar.gz has directory entries (%d dirs)" % len(dir_entries))
    for needed in ("usr", "usr/bin", "usr/lib", "usr/share/applications"):
        check(needed in [d.rstrip("/") for d in dir_entries],
              "directory entry ./%s/ present" % needed)

    world_writable = [key for key in keys
                      if data_members[key][2] and data_members[key][0] & 0o002]
    check(not world_writable,
          "no world-writable files (%s)" % (", ".join(world_writable[:3]) or "none"))
    world_writable_dirs = [key for key in dir_entries if data_members[key][0] & 0o022]
    check(not world_writable_dirs,
          "no group/other-writable dirs (%s)"
          % (", ".join(world_writable_dirs[:3]) or "none"))

    # The 0.7.38 failure in one assertion: every file's parent chain must be an
    # explicit directory entry, because dpkg never invents a parent itself.
    dir_set = {name.rstrip("/") for name in dir_entries}
    missing_parents = []
    for key in keys:
        if not data_members[key][2]:
            continue
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        while parent:
            if parent not in dir_set:
                missing_parents.append("%s (parent ./%s/)" % (key, parent))
                break
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    check(not missing_parents,
          "every file's parent directory is an entry (%s)"
          % (", ".join(missing_parents[:3]) or "none missing"))

    for size in (48, 128, 256):
        check("usr/share/icons/hicolor/%dx%d/apps/%s.png" % (size, size, PACKAGE)
              in data_members, "%dx%d hicolor icon installed" % (size, size))

    launcher = "usr/bin/" + PACKAGE
    check(launcher in data_members, "%s present" % launcher)
    if launcher in data_members:
        mode = data_members[launcher][0]
        check(mode & 0o111, "%s is executable (mode %o)" % (launcher, mode))

    check("usr/lib/%s/mihome_pc/cli.py" % PACKAGE in data_members,
          "package payload present")
    # 型号目录/卡片布局来自安卓包，已按所有者决定彻底移除；
    # 这里反过来断言它们**不在**包里。
    for name in ("catalog.json", "catalog_categories.json", "card_layouts.json"):
        check("usr/lib/%s/mihome_pc/assets/%s" % (PACKAGE, name) not in data_members,
              "%s removed (no redistribution rights)" % name)
    check("usr/lib/%s/mihome_pc/assets/dynamic_ranges.json" % PACKAGE in data_members,
          "measured ranges shipped")
    check("usr/lib/%s/mihome_pc/assets/demo/MAP.json" % PACKAGE in data_members,
          "demo assets shipped")
    check("usr/lib/%s/mihome_pc/static/index.html" % PACKAGE in data_members,
          "web UI assets shipped")
    check("usr/share/applications/%s.desktop" % PACKAGE in data_members,
          "desktop entry present")
    desktop_text = read_member(payload["data.tar.gz"],
                               "usr/share/applications/%s.desktop" % PACKAGE)
    check("Exec=%s app" % PACKAGE in desktop_text,
          "menu entry launches `app` (a window/tray entry, not bare serve)")
    check("Icon=%s" % PACKAGE in desktop_text,
          "menu entry references the icon that is actually installed")
    check("usr/lib/systemd/user/%s.service" % PACKAGE in data_members,
          "systemd user unit present")
    check("usr/share/doc/%s/copyright" % PACKAGE in data_members,
          "copyright present")

    check(not any("__pycache__" in key or key.endswith(".pyc") for key in keys),
          "no __pycache__ / .pyc shipped")
    check(not any("reverse/" in key or key.startswith("tests/") for key in keys),
          "no reverse-engineering workspace or tests shipped")

    # every shipped module must parse (syntax gate under this interpreter)
    import ast
    import gzip
    from io import BytesIO
    bad = []
    with tarfile.open(fileobj=BytesIO(gzip.decompress(payload["data.tar.gz"]))) as archive:
        for member in archive.getmembers():
            name = member.name[2:] if member.name.startswith("./") else member.name
            if not name.endswith(".py"):
                continue
            source = archive.extractfile(member).read().decode("utf-8")
            try:
                ast.parse(source, filename=name)
            except SyntaxError as exc:
                bad.append("%s: %s" % (name, exc))
    check(not bad, "all shipped .py files parse (%d checked)" % sum(
        1 for key in keys if key.endswith(".py")))

    print("")
    print("tarball: %s" % TARBALL)
    if os.path.exists(TARBALL):
        import gzip
        from io import BytesIO
        with open(TARBALL, "rb") as handle:
            blob = gzip.decompress(handle.read())
        with tarfile.open(fileobj=BytesIO(blob)) as archive:
            entries = archive.getmembers()
            tar_names = [m.name for m in entries]
        check("install.sh" in tar_names, "install.sh present")
        check("lib/mihome_pc/cli.py" in tar_names, "package payload present")
        check(any(m.isdir() for m in entries),
              "tarball has directory entries (same unpacking trap as the deb)")
        check(any(n.endswith("/%s.desktop" % PACKAGE) or n == "%s.desktop" % PACKAGE
                  for n in tar_names),
              "tarball ships the .desktop menu entry")
        for size in (48, 128, 256):
            pattern = "share/icons/hicolor/%dx%d/apps/%s.png" % (size, size, PACKAGE)
            check(pattern in tar_names, "tarball ships the %dx%d PNG icon" % (size, size))
        bad = [n for n, info in ((m.name, m) for m in entries)
               if info.isfile() and info.mode & 0o002]
        check(not bad, "no world-writable files in the tarball (%s)"
              % (", ".join(bad[:3]) or "none"))
    else:
        check(False, "tarball exists")

    print("")
    if failures:
        print("FAILED: %d check(s)" % len(failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
