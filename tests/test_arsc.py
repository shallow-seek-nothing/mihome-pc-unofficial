"""Resources.arsc text extraction: locale preference and entry parsing.

The parser is pure offsets, so the test builds a tiny table by hand instead of
shipping a plugin binary: one global value pool, one package with a ``string``
type, and two locale chunks (``default`` and ``zh-rTW``).  Whoever touches the
offsets gets an immediate failure instead of silently mis-labelled text.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.xiaomi import arsc  # noqa: E402


def _string_pool(strings, utf8=True):
    """RES_STRING_POOL_TYPE chunk (no styles)."""
    header = 28
    offsets = []
    blob = b""
    for text in strings:
        offsets.append(len(blob))
        if utf8:
            encoded = text.encode("utf-8")
            blob += bytes([len(text), len(encoded)]) + encoded + b"\x00"
        else:
            encoded = text.encode("utf-16-le")
            blob += struct.pack("<H", len(text)) + encoded + b"\x00\x00"
    strings_start = header + 4 * len(strings)
    body = b"".join(struct.pack("<I", off) for off in offsets) + blob
    flags = 0x100 if utf8 else 0
    size = header + len(body)
    head = struct.pack("<HHIIIIII", 0x0001, header, size, len(strings), 0,
                       flags, strings_start, 0)
    return head + body


def _config(locale):
    """64-byte ResTable_config; only the language/region bytes are set."""
    raw = bytearray(64)
    struct.pack_into("<I", raw, 0, 64)
    if locale != "default":
        lang, _, region = locale.partition("-r")
        raw[8:10] = lang.encode("ascii")
        if region:
            raw[10:12] = region.encode("ascii")
    return bytes(raw)


def _type_chunk(type_id, keys, entries, global_pool, locale):
    """One RES_TABLE_TYPE_TYPE chunk: ``entries`` maps key -> string index."""
    config = _config(locale)
    header = 20 + len(config)
    entry_count = len(keys)
    payload = b""
    offsets = []
    for key in keys:
        index = entries.get(key)
        if index is None:
            offsets.append(0xFFFFFFFF)
            continue
        offsets.append(len(payload))
        key_index = keys.index(key)
        entry = struct.pack("<HHI", 8, 0, key_index)
        entry += struct.pack("<HBBI", 8, 0, 0x03, index)
        payload += entry
    offsets_blob = b"".join(struct.pack("<I", off) for off in offsets)
    entries_start = header + len(offsets_blob)
    body = config + offsets_blob + payload
    head = struct.pack("<HHIBBHII", 0x0201, header, 20 + len(body),
                       type_id, 0, 0, entry_count, entries_start)
    return head + body


def build_table(default_texts, locale_texts, extra_locale="zh-rTW"):
    keys = sorted(set(default_texts) | set(locale_texts))
    pool_texts = []
    index_of = {}

    def index(text):
        if text not in index_of:
            index_of[text] = len(pool_texts)
            pool_texts.append(text)
        return index_of[text]

    default_entries = {key: index(text) for key, text in default_texts.items()}
    locale_entries = {key: index(text) for key, text in locale_texts.items()}
    global_pool = _string_pool(pool_texts)
    type_pool = _string_pool(["string"], utf8=False)
    key_pool = _string_pool(keys)

    chunks = [_type_chunk(1, keys, default_entries, global_pool, "default"),
              _type_chunk(1, keys, locale_entries, global_pool, extra_locale)]

    package_body = type_pool + key_pool + b"".join(chunks)
    package_header = 288
    name = "com.example.plugin".encode("utf-16-le").ljust(256, b"\x00")
    package = struct.pack("<HHI", 0x0200, package_header,
                          package_header + len(package_body))
    package += struct.pack("<I", 0x7F)
    package += name
    # typeStrings / keyStrings are offsets *from the package chunk start*
    package += struct.pack("<IIII", package_header,
                           len(["string"]),
                           package_header + len(type_pool),
                           len(keys))
    package += struct.pack("<I", 0)
    package += package_body

    table_body = global_pool + package
    table_header = 12                      # chunk header + packageCount
    table = struct.pack("<HHI", 0x0002, table_header, table_header + len(table_body))
    table += struct.pack("<I", 1)         # packageCount
    return table + table_body


class TestLocalePreference(unittest.TestCase):
    def setUp(self):
        self.data = build_table(
            {"pause": "\u5df2\u6682\u505c", "only_default": "\u4ec5\u7b80\u4f53"},
            {"pause": "\u5df2\u66ab\u505c", "only_tw": "\u50c5\u7e41\u9ad4"})

    def test_simplified_beats_traditional(self):
        texts = arsc.table_texts(self.data)
        self.assertEqual(texts["pause"], "\u5df2\u6682\u505c")

    def test_missing_in_preferred_locale_falls_back(self):
        texts = arsc.table_texts(self.data)
        self.assertEqual(texts["only_default"], "\u4ec5\u7b80\u4f53")
        self.assertEqual(texts["only_tw"], "\u50c5\u7e41\u9ad4")

    def test_keep_all_returns_every_locale(self):
        everything = arsc.table_texts(self.data, keep_all=True)
        self.assertEqual(everything["pause"]["default"], "\u5df2\u6682\u505c")
        self.assertEqual(everything["pause"]["zh-rTW"], "\u5df2\u66ab\u505c")

    def test_locale_of_config_is_read(self):
        self.assertEqual(arsc.config_locale(_config("zh-rTW"), 0), "zh-rTW")
        self.assertEqual(arsc.config_locale(_config("default"), 0), "default")
        self.assertEqual(arsc.config_locale(_config("en"), 0), "en")

    def test_forced_locale_wins(self):
        texts = arsc.table_texts(self.data, prefer=("zh-rTW",))
        self.assertEqual(texts["pause"], "\u5df2\u66ab\u505c")

    def test_has_cjk(self):
        self.assertTrue(arsc.has_cjk("\u5df2\u6682\u505c"))
        self.assertFalse(arsc.has_cjk("pause"))


if __name__ == "__main__":
    unittest.main()
