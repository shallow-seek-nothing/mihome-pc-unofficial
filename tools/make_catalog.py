"""从 APK 内置的 plugin_config 生成客户端型号目录。

输入：reverse/apk/unpack/assets/device_config/plugin_config_cn-zh_cn
      （APK 构建期缓存的全国区设备插件总表，明文 JSON，12,160 条）
输出：mihome_pc/assets/catalog.json          型号 → 展示/分类元数据
      mihome_pc/assets/catalog_categories.json  品类索引

设计取舍：
  * 键名压到最短，体积优先（12k 条全量进包）
  * 丢掉对客户端无用的运营字段（rank/channel/verified_time 等）
  * 保留 display_type_list（卡片类型）与 bt_*/mesh/matter 标志，
    它们决定"这台设备走 LAN / BLE / 网关 / 云"
  * desc 只保留前 80 字，避免体积被长文案吃掉

用法： python tools/make_catalog.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "reverse", "apk", "unpack", "assets", "device_config",
                   "plugin_config_cn-zh_cn")
# 输出到**用户数据目录**，不写进仓库：这份数据来自安卓包，不随项目发布。
# 生成后仅供本机使用，请勿公开再分发。
sys.path.insert(0, ROOT)
from mihome_pc.cli import data_dir  # noqa: E402

OUT_DIR = os.path.join(data_dir(), "catalog")

# 输出字段 → 源字段（源字段名在 APK 里是驼峰/下划线混用）
FIELDS = [
    ("n", "name"),              # 显示名（中文）
    ("b", "brand_name"),        # 品牌
    ("c", "cate_name"),         # 品类名（中文）
    ("s", "subcategory_id"),    # 子品类 id
    ("p", "pid"),               # product id
    ("d", "pd_id"),             # plugin/product center id
    ("t", "sc_type"),           # 设备大类
    ("dl", "display_type_list"),  # 卡片类型列表
    ("ic", "icon_real"),        # 产品图 URL
    ("v", "min_app_version"),
]

FLAGS = [
    ("bt", "bt_bind_style"),       # 蓝牙绑定方式（0=不支持）
    ("bts", "bt_is_secure"),
    ("btg", "bt_gateway"),
    ("mg", "mesh_gateway"),
    ("zg", "zigbee_gateway"),
    ("pg", "plc_gateway"),
    ("matter", "is_matter_type"),
    ("mgw", "matter_gateway"),
    ("pxg", "proxy_gateway"),
    ("mwg", "is_mesh_wifi_device"),
    ("uwb", "support_uwb"),
    ("fiveg", "fiveG_wifi"),
    ("perm", "permission_control"),
]


def main():
    if not os.path.exists(SRC):
        raise SystemExit("找不到 %s，先跑 reverse/tools/_unpack.py" % SRC)
    with open(SRC, encoding="utf-8") as f:
        payload = json.load(f)

    devices = payload["result"]["devices"]["list"]
    print("源条目: %d" % len(devices))

    catalog = {}
    cats = {}
    skipped = 0

    for e in devices:
        model = (e.get("model") or "").strip()
        if not model:
            skipped += 1
            continue
        rec = {}
        for short, src in FIELDS:
            val = e.get(src)
            if val in (None, "", [], 0, 0.0, False):
                continue
            if short == "ic":
                # 图片 URL 带签名，太长；只留路径，客户端自行拼 CDN 前缀
                val = _icon_path(val)
            rec[short] = val
        for short, src in FLAGS:
            val = e.get(src)
            if val:                      # 只记录"非零"标志，省体积
                rec[short] = val
        desc = (e.get("desc") or "").strip()
        if desc:
            rec["ds"] = desc[:80]
        catalog[model] = rec

        cate = e.get("cate_name") or "其他"
        cats.setdefault(cate, 0)
        cats[cate] += 1

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    print("输出目录：%s（仅本机使用，请勿公开再分发）" % OUT_DIR)
    out = os.path.join(OUT_DIR, "catalog.json")
    with open(out, "w", encoding="utf-8") as f:
        # ensure_ascii=False 让中文可读；sorted 保证可复现
        json.dump(catalog, f, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"))
    size = os.path.getsize(out)
    print("写出 %s：%d 型号，%.2f MB（跳过 %d）"
          % (out, len(catalog), size / 1048576.0, skipped))

    cats_sorted = sorted(cats.items(), key=lambda kv: -kv[1])
    out2 = os.path.join(OUT_DIR, "catalog_categories.json")
    with open(out2, "w", encoding="utf-8") as f:
        json.dump([[k, v] for k, v in cats_sorted], f,
                  ensure_ascii=False, separators=(",", ":"))
    print("写出 %s：%d 个品类" % (out2, len(cats_sorted)))
    print("品类 top10: %s" % ", ".join("%s(%d)" % (k, v) for k, v in cats_sorted[:10]))


def _icon_path(url):
    """https://cnbj1.fds.api.xiaomi.com/iotweb-product-center/5621.png?Signature=..."""
    if not isinstance(url, str):
        return url
    q = url.find("?")
    return url[:q] if q > 0 else url


if __name__ == "__main__":
    main()
