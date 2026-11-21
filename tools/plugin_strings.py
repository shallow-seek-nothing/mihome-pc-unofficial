"""Every string in a plugin's ``resources.arsc`` (flat, de-duplicated).

This is the quick "what text does this plugin ship" view: no resource names,
no locale selection, just the pool.  For ``name -> 简体中文`` use
``tools/arsc_map.py``; for "which of these names does the code actually use"
use ``tools/plugin_texts.py``.

The chunk format and its pitfalls are documented in
``mihome_pc/xiaomi/arsc.py`` (the parser this tool wraps).

Usage:
    python tools/plugin_strings.py <pkg>
    python tools/plugin_strings.py <pkg> --grep 分钟 温度 --limit 40
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mihome_pc.xiaomi import arsc  # noqa: E402


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    package = argv[1]
    keywords = []
    limit = 60
    index = 2
    while index < len(argv):
        if argv[index] == "--grep":
            index += 1
            while index < len(argv) and not argv[index].startswith("--"):
                keywords.append(argv[index])
                index += 1
        elif argv[index] == "--limit":
            limit = int(argv[index + 1])
            index += 2
        else:
            index += 1
    strings = arsc.package_strings(package)
    unique = []
    seen = set()
    for item in strings:
        clean = item.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        if keywords and not any(k in clean for k in keywords):
            continue
        unique.append(clean)
    print("资源字符串总数: %d，去重后 %d，命中过滤 %d"
          % (len(strings), len(seen), len(unique)))
    for item in unique[:limit]:
        print("  " + item)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
