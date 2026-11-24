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

    data_members = read_tar_members(payload["data.tar.gz"])
    keys = sorted(data_members)
    print("  data.tar.gz: %d entries" % len(keys))

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
    check("usr/lib/systemd/user/%s.service" % PACKAGE in data_members,
          "systemd user unit present")
    check("usr/share/doc/%s/copyright" % PACKAGE in data_members,
          "copyright present")
    for relative in ("LICENSE.txt", "THIRD_PARTY_NOTICES.md", "LICENSES/MIT.txt"):
        check("usr/share/doc/%s/%s" % (PACKAGE, relative) in data_members,
              "%s present" % relative)

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
        with open(TARBALL, "rb") as handle:
            blob = gzip.decompress(handle.read())
        from io import BytesIO
        with tarfile.open(fileobj=BytesIO(blob)) as archive:
            tar_names = [m.name for m in archive.getmembers()]
        check("install.sh" in tar_names, "install.sh present")
        check("lib/mihome_pc/cli.py" in tar_names, "package payload present")
        for relative in ("LICENSE.txt", "THIRD_PARTY_NOTICES.md", "LICENSES/MIT.txt"):
            check(relative in tar_names, "tarball has %s" % relative)
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
