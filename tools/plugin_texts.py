"""Resource names, their text, and which of them the plugin code uses.

Two questions this answers:

  1. ``name -> 简体中文`` for every ``string`` resource.  A plugin ships ~95
     locale chunks per resource, so picking the right one matters (see
     ``LOCALE_PREFERENCE`` in ``mihome_pc/xiaomi/arsc.py``).
  2. Which names does the decompiled code actually reference
     (``R.string.<name>``)?  Everything else is either used from XML, resolved
     by the host app, or simply dead -- the 2026-11-21 finding was that ALL
     148 unreferenced names are nonetheless present in ``classes.dex`` as
     literals, i.e. the plugin keeps a name table for someone else to resolve.

Usage:
    python tools/plugin_texts.py <pkg> --grep 筒自洁
    python tools/plugin_texts.py <pkg> --out texts.json
    python tools/plugin_texts.py <pkg> --usage <jadx sources dir>
"""
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mihome_pc.xiaomi import arsc  # noqa: E402

REFERENCE = re.compile(r"R\.string\.([A-Za-z0-9_]+)")


def referenced_names(source_dir):
    """Names used as ``R.string.<name>`` anywhere under ``source_dir``."""
    used = set()
    for base, _dirs, files in os.walk(source_dir):
        for name in files:
            if not name.endswith(".java"):
                continue
            with open(os.path.join(base, name), encoding="utf-8",
                      errors="replace") as handle:
                text = handle.read()
            if "R.string." in text:
                used.update(REFERENCE.findall(text))
    return used


def dex_literals(package, names):
    """Subset of ``names`` that appear literally in the package's dex."""
    with zipfile.ZipFile(package) as archive:
        blob = archive.read("classes.dex")
    return {name for name in names if name.encode("utf-8") in blob}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    package = argv[1]
    keywords = []
    out_path = None
    source_dir = None
    index = 2
    while index < len(argv):
        if argv[index] == "--grep":
            index += 1
            while index < len(argv) and not argv[index].startswith("--"):
                keywords.append(argv[index])
                index += 1
        elif argv[index] == "--out":
            out_path = argv[index + 1]
            index += 2
        elif argv[index] == "--usage":
            source_dir = argv[index + 1]
            index += 2
        else:
            index += 1

    texts = arsc.package_texts(package)
    chinese = {name: text for name, text in texts.items() if arsc.has_cjk(text)}
    print("string resources: %d (with Chinese: %d)" % (len(texts), len(chinese)))
    if out_path:
        folder = os.path.dirname(out_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(chinese, handle, ensure_ascii=False, indent=1)
        print("wrote " + out_path)

    if source_dir:
        used = referenced_names(source_dir)
        missing = [name for name in texts if name not in used]
        as_literal = dex_literals(package, missing)
        print("referenced via R.string.: %d" % len([n for n in texts if n in used]))
        print("unreferenced: %d (present as dex literals: %d)"
              % (len(missing), len(as_literal)))
        target = "clean_mode_can_not_order"
        if target in texts:
            print("%s -> %r referenced=%s literal=%s"
                  % (target, texts[target], target in used, target in as_literal))

    if not keywords:
        return 0
    shown = 0
    for name, text in chinese.items():
        if not any(needle in text for needle in keywords):
            continue
        print("  %-40s %s" % (name, text.replace("\n", " / ")[:70]))
        shown += 1
        if shown >= 40:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
