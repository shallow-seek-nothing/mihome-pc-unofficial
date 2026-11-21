"""Pre-push gate: no personal identifiers may reach the public tree."""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PATTERNS = [
    ("home id", re.compile(r"\b2210\d{8}\b")),
    ("uid", re.compile(r"\b223\d{7}\b")),
    ("device did", re.compile(r"(?<!-)\b\d{9,10}\b")),
    ("ble did", re.compile(r"\bblt\.[0-9a-z.]+\b")),
    ("lan ip", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")),
    ("mac", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")),
    ("long hex", re.compile(r"\b[0-9a-f]{24,}\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("windows user", re.compile(r"C:\\Users\\[A-Za-z0-9_.-]+", re.I)),
]

TEXT_EXT = (".py", ".md", ".txt", ".js", ".html", ".css", ".ps1", ".cmd",
            ".json", ".vbs", ".yml", ".yaml", ".toml")

SKIP_DIRS = (".git", "__pycache__", ".ruff_cache", ".toolchain", "reverse",
             "dist", "out", "data", ".dsh-project-memory", "_publish", "release")

# Tests and fixtures legitimately contain numbers that look like device ids
# (timestamps, error codes, AES vectors). Those two classes are not useful
# signals there, so they are only enforced outside tests/.
TEST_RELAXED = ("device did", "long hex")

# Files that never ship (see .gitignore) -- kept locally, so the gate skips them.
SKIP_FILES = {
    "wol_timing_test.py", "probe_fryer_limits.py", "probe_tv_state.py",
    "do_login.py", "03-真机行为记录.md",
}

# Obviously fake fixtures: the doc-example MAC and AEStest vectors.
FAKE_VALUES = {"AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff",
               "1700000000", "noreply@example.invalid",
               "git@github.com"}


def iter_files(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(TEXT_EXT) and name not in SKIP_FILES:
                yield os.path.join(folder, name)


def scan(paths):
    """[(path, line, kind, value, snippet)] for every suspicious line."""
    hits = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.replace(os.sep, "/")
        in_tests = ("/tests/" in rel or "/tests\\" in rel
                    or "/assets/" in rel or "/assets\\" in rel)
        for number, line in enumerate(lines, start=1):
            for kind, pattern in PATTERNS:
                if in_tests and kind in TEST_RELAXED:
                    continue
                match = pattern.search(line)
                if match and match.group(0) not in FAKE_VALUES:
                    hits.append((path, number, kind, match.group(0),
                                 line.strip()[:100]))
                    break
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()
    hits = scan(iter_files(args.root))
    for path, number, kind, value, snippet in hits:
        rel = os.path.relpath(path, args.root)
        print("  %-12s %-24s %s:%d  %s" % (kind, value, rel, number, snippet[:50]))
    print("sanitize_check: %d suspicious line(s)" % len(hits))
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main())
