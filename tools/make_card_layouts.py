"""Extract the device-card layout table from the APK.

Source: ``assets/cardControl/configDes.json`` -- a cached cloud response
(``card_control_config`` v11, ``zh_CN``) whose ``result.content`` is a JSON
string holding 89 entries of::

    {props:  [{supportType: [1|2|3]}],
     cards:  {layout_type: 0..5, card_items: [{cardType: N}]},
     models: [ "vendor.model", ... ]}

That is Xiaomi's own "which card shape do these models get" table.  We flatten
it to ``model -> {l, ci, st}`` and keep it small.

    model -> {"l": layout_type, "ci": [cardType...], "st": [supportType...]}

Usage: python tools/make_card_layouts.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "reverse", "apk", "unpack", "assets", "cardControl",
                   "configDes.json")
# 同 make_catalog.py：输出到用户数据目录的 catalog/，不进仓库、不随包发布。
sys.path.insert(0, ROOT)
from mihome_pc.cli import data_dir  # noqa: E402

OUT = os.path.join(data_dir(), "catalog", "card_layouts.json")


def main():
    with open(SRC, encoding="utf-8") as handle:
        payload = json.load(handle)
    result = payload.get("result") or {}
    content = json.loads(result.get("content") or "[]")
    print("source entries: %d (version %s)" % (len(content), result.get("version")))

    table = {}
    for entry in content:
        cards = entry.get("cards") or {}
        layout = cards.get("layout_type")
        card_types = [item.get("cardType") for item in cards.get("card_items") or []
                      if item.get("cardType") is not None]
        support = []
        for prop in entry.get("props") or ():
            support.extend(prop.get("supportType") or ())
        record = {}
        if layout is not None:
            record["l"] = layout
        if card_types:
            record["ci"] = sorted(set(card_types))
        if support:
            record["st"] = sorted(set(support))
        for model in entry.get("models") or ():
            if model:
                table[model] = record

    if not os.path.isdir(os.path.dirname(OUT)):
        os.makedirs(os.path.dirname(OUT))
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(table, handle, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"))
    print("wrote %s: %d models, %.1f KB"
          % (OUT, len(table), os.path.getsize(OUT) / 1024.0))
    layouts = {}
    for record in table.values():
        layouts[record.get("l")] = layouts.get(record.get("l"), 0) + 1
    print("layout_type distribution: %s"
          % sorted(layouts.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    main()
