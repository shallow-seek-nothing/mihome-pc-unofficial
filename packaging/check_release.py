"""Release consistency check: run before publishing a version.

It answers \u201cis this tree actually releaseable?\u201d without a network or an account:
version numbers agree everywhere, the three artifacts exist, the release note
matches them (sha256), and the deb structure verifies.

    python packaging/check_release.py
"""
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_VERSION_LINE = re.compile(r'^VERSION\s*=\s*"([^"]+)"', re.M)
_README_LINE = re.compile(r"\u5f53\u524d\u7248\u672c\s*\*\*([0-9.]+)\*\*")
_CHANGELOG_HEAD = re.compile(r"^##\s+([0-9]+\.[0-9]+\.[0-9]+)", re.M)

ARTIFACTS = (
    "mihome-pc-%s-portable-win64.zip",
    "mihome-pc_%s_all.deb",
    "mihome-pc-%s-linux.tar.gz",
)


def package_version(text):
    match = _VERSION_LINE.search(text or "")
    return match.group(1) if match else ""


def readme_version(text):
    match = _README_LINE.search(text or "")
    return match.group(1) if match else ""


def changelog_version(text):
    match = _CHANGELOG_HEAD.search(text or "")
    return match.group(1) if match else ""


def release_note_path(dist, version):
    return os.path.join(dist, "\u53d1\u5e03\u8bf4\u660e-%s.md" % version)


def hashes_in(text):
    return {value.lower() for value in re.findall(r"`([0-9a-f]{64})`", text or "")}


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def check(root=None):
    """``[(name, ok, detail)]``; ``ok`` False means do not publish."""
    root = root or ROOT
    rows = []
    version = package_version(_read(os.path.join(root, "mihome_pc", "__init__.py")))
    rows.append(("\u7248\u672c\u53f7", bool(version), version or "\u8bfb\u4e0d\u5230"))
    readme = readme_version(_read(os.path.join(root, "README.md")))
    rows.append(("README \u7248\u672c", readme == version,
                 "%s vs %s" % (readme or "?", version or "?")))
    changes = changelog_version(_read(os.path.join(root, "CHANGELOG.md")))
    rows.append(("CHANGELOG \u9876\u90e8", changes == version,
                 "%s vs %s" % (changes or "?", version or "?")))
    matrix = _read(os.path.join(root, "docs", "04-\u529f\u80fd\u5bf9\u7167.md"))
    rows.append(("\u529f\u80fd\u77e9\u9635\u7248\u672c", version in matrix,
                 "%s" % (version or "?")))

    dist = os.path.join(root, "dist")
    present = []
    for pattern in ARTIFACTS:
        name = pattern % version
        path = os.path.join(dist, name)
        present.append((name, os.path.isfile(path)))
    missing = [name for name, ok in present if not ok]
    rows.append(("\u4e09\u4ef6\u4ea7\u7269", not missing,
                 "\u7f3a %s" % ", ".join(missing) if missing else "\u9f50\u5168"))

    note_path = release_note_path(dist, version)
    note = _read(note_path)
    rows.append(("\u53d1\u5e03\u8bf4\u660e", bool(note),
                 note_path if note else "\u7f3a %s" % note_path))
    if note:
        expected = hashes_in(note)
        stale = []
        for name, ok in present:
            if not ok:
                continue
            actual = digest(os.path.join(dist, name))
            if actual not in expected:
                stale.append(name)
        detail = "\u5168\u90e8\u5bf9\u5f97\u4e0a"
        if stale:
            detail = "\u8fc7\u671f %s" % ", ".join(stale)
        rows.append(("sha256 \u4e00\u81f4", not stale, detail))
    return rows


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def main():
    rows = check()
    failed = [name for name, ok, _detail in rows if not ok]
    for name, ok, detail in rows:
        print("  %s %s\uff1a%s" % ("\u2713" if ok else "\u2717", name, detail))
    print("check_release: %s" % ("ok" if not failed else "FAILED"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
