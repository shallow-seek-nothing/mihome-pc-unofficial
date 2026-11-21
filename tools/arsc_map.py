"""Map Android resource names to their text inside a plugin ``resources.arsc``.

Why this exists: the plugin's code only refers to numeric ids, so to find
"which path shows 筒自洁提醒" we need ``<resource name> -> <text>`` pairs.

The parsing itself lives in the product module (``mihome_pc.xiaomi.arsc``);
this tool is the command-line face of it plus a structure dump for debugging.

Reading that format took a while, so the pitfalls are written down here:

  * ``string`` is not one chunk.  minij.washer.v5 ships ~95 of them, one per
    locale; walking in file order and letting the last win lands on
    ``zh-rTW`` and the whole table comes out in Traditional Chinese.
  * Each type chunk repeats ``entryCount`` fixed-width offsets and the entries
    live at ``entriesStart``; ``0xFFFFFFFF`` marks a hole.
  * ``flags & 1`` marks a complex (bag) entry -- styles/arrays -- skip those.

Usage:
    python tools/arsc_map.py <pkg> --stats            # structure + chunk counts
    python tools/arsc_map.py <pkg> --grep 筒自洁       # search by text
    python tools/arsc_map.py <pkg> --out res-map.json # name -> text (zh-CN)
    python tools/arsc_map.py <pkg> --out all.json --all-configs
    python tools/arsc_map.py <pkg> --locale zh-rTW    # force one locale
"""
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mihome_pc.xiaomi import arsc  # noqa: E402


def read_table(package):
    with zipfile.ZipFile(package) as archive:
        return archive.read("resources.arsc")


def stats(package):
    data = read_table(package)
    print("%s: %d bytes of resources.arsc" % (package, len(data)))
    all_configs = arsc.table_texts(data, keep_all=True)
    locales = {}
    for per_locale in all_configs.values():
        for locale in per_locale:
            locales[locale] = locales.get(locale, 0) + 1
    print("string resources: %d" % len(all_configs))
    print("locales: %d" % len(locales))
    favourite = sorted(locales.items(), key=lambda kv: -kv[1])[:8]
    print("  most complete: %s" % ", ".join("%s(%d)" % kv for kv in favourite))
    table = arsc.table_texts(data)
    chinese = sum(1 for text in table.values() if arsc.has_cjk(text))
    print("picked by preference: %d entries, %d with Chinese" % (len(table), chinese))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    package = argv[1]
    keywords = []
    out_path = None
    locale = None
    keep_all = False
    show_stats = False
    index = 2
    while index < len(argv):
        flag = argv[index]
        if flag == "--grep":
            index += 1
            while index < len(argv) and not argv[index].startswith("--"):
                keywords.append(argv[index])
                index += 1
        elif flag == "--out":
            out_path = argv[index + 1]
            index += 2
        elif flag == "--locale":
            locale = argv[index + 1]
            index += 2
        elif flag == "--all-configs":
            keep_all = True
            index += 1
        elif flag == "--stats":
            show_stats = True
            index += 1
        else:
            index += 1

    data = read_table(package)
    if show_stats:
        stats(package)
    prefer = (locale,) if locale else arsc.LOCALE_PREFERENCE
    mapping = arsc.table_texts(data, prefer=prefer, keep_all=keep_all)
    if not keep_all:
        print("资源条目 %d 条" % len(mapping))
    if out_path:
        folder = os.path.dirname(out_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(mapping, handle, ensure_ascii=False, indent=1)
        print("已写出 " + out_path)
    if keep_all:
        sample = list(mapping.items())[:1]
        for name, per_locale in sample:
            print("  %s -> %d locales" % (name, len(per_locale)))
        return 0
    shown = 0
    for key, value in mapping.items():
        if keywords and not any(needle in value for needle in keywords):
            continue
        print("  %-40s %s" % (key, value.replace("\n", " / ")[:70]))
        shown += 1
        if shown >= 40:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
