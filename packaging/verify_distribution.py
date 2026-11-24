"""Verify legal notices and restricted-content boundaries in all artifacts.

The checks inspect archive members directly; a local .gitignore rule or a clean
source checkout is not evidence that a built package is safe to distribute.

Usage: python packaging/verify_distribution.py
"""
import io
import os
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_deb import VERSION, read_ar  # noqa: E402
from distribution_policy import restricted_members  # noqa: E402

LEGAL_MEMBERS = ("LICENSE.txt", "THIRD_PARTY_NOTICES.md", "LICENSES/MIT.txt")
ARTIFACTS = (
    ("mihome-pc-%s-portable-win64.zip", "win64"),
    ("mihome-pc-%s-portable-win32.zip", "win32"),
    ("mihome-pc_%s_all.deb", "deb"),
    ("mihome-pc-%s-linux.tar.gz", "tarball"),
)


def _normal(name):
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name


def _find_member(names, suffix):
    return [name for name in names
            if _normal(name) == suffix or _normal(name).endswith("/" + suffix)]


def _check_members(label, names, read_member, windows=False):
    errors = []
    blocked = restricted_members(names)
    if blocked:
        errors.append("restricted content: " + ", ".join(blocked))

    found = {}
    for required in LEGAL_MEMBERS:
        matches = _find_member(names, required)
        if not matches:
            errors.append("missing " + required)
        else:
            found[required] = matches[0]
    if windows and not _find_member(names, "python38/LICENSE.txt"):
        errors.append("missing python38/LICENSE.txt")

    if "LICENSE.txt" in found:
        text = read_member(found["LICENSE.txt"]).decode("utf-8")
        if "PolyForm Noncommercial License 1.0.0" not in text:
            errors.append("LICENSE.txt is not PolyForm Noncommercial 1.0.0")
        if "Required Notice:" not in text:
            errors.append("LICENSE.txt has no Required Notice")
    if errors:
        raise ValueError(label + ":\n  - " + "\n  - ".join(errors))


def verify_archive(path, kind=None):
    """Raise ValueError on the first failed check; return the verified path."""
    if not os.path.isfile(path):
        raise ValueError("missing artifact: %s" % path)
    name = os.path.basename(path)
    kind = kind or ("deb" if name.endswith(".deb") else
                    "tarball" if name.endswith(".tar.gz") else
                    "win32" if "win32" in name else "win64")

    if kind in ("win64", "win32"):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            _check_members(name, names, archive.read, windows=True)
    elif kind == "tarball":
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
            _check_members(name, names,
                           lambda member: archive.extractfile(member).read())
    elif kind == "deb":
        payload = dict(read_ar(path))
        data = payload["data.tar.gz"]
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            names = archive.getnames()
            _check_members(name, names,
                           lambda member: archive.extractfile(member).read())
    else:
        raise ValueError("unknown artifact kind: %s" % kind)
    return path


def verify_all(dist=None, version=VERSION):
    dist = dist or os.path.join(ROOT, "dist")
    verified = []
    for pattern, kind in ARTIFACTS:
        path = os.path.join(dist, pattern % version)
        verified.append(verify_archive(path, kind))
    return verified


def main():
    try:
        verified = verify_all()
    except (KeyError, OSError, ValueError, tarfile.TarError,
            zipfile.BadZipFile) as exc:
        print("DISTRIBUTION CHECK FAILED\n%s" % exc)
        return 1
    for path in verified:
        print("  ok  %s" % os.path.basename(path))
    print("ALL DISTRIBUTION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
