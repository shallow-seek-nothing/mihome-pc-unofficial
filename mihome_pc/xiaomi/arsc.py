"""Read the text out of an Android ``resources.arsc`` (pure stdlib).

A device plugin keeps its user-facing text in the resource table, not in the
React Native bundle, so this is how the client gets official labels for a model
without any Android tooling.  Only the string pools are needed; the full
resource-name mapping is still being worked out (see docs/reverse/04).

Format reminder::

    RES_TABLE_TYPE (0x0002)
      RES_STRING_POOL_TYPE (0x0001)   global value pool
      RES_TABLE_PACKAGE_TYPE (0x0200)
        ... type/key pools and per-type entry chunks

Usage::

    from mihome_pc.xiaomi import arsc
    text = arsc.package_strings("data/plugins/minij.washer.v5/package.zip")
"""
import struct
import zipfile

__all__ = ["package_strings", "table_strings", "chinese_strings",
           "package_texts", "table_texts", "config_locale", "has_cjk",
           "LOCALE_PREFERENCE"]


def has_cjk(text):
    """True when ``text`` contains at least one CJK ideograph."""
    return any(_CJK_LOW <= ch <= _CJK_HIGH for ch in text or "")

RES_STRING_POOL_TYPE = 0x0001
RES_TABLE_TYPE = 0x0002
RES_TABLE_PACKAGE_TYPE = 0x0200
RES_TABLE_TYPE_TYPE = 0x0201
UTF8_FLAG = 1 << 8
FLAG_COMPLEX = 1
TYPE_STRING = 0x03

# A plugin's resource table ships one ``string`` chunk per locale (~95 of them
# in minij.washer.v5).  Walking them in file order and letting the last one win
# ends on zh-rTW, i.e. Traditional Chinese -- which is how the first version of
# tools/arsc_map.py "lost" the Simplified text.  Pick by preference instead.
LOCALE_PREFERENCE = ("zh-rCN", "zh", "default", "zh-rSG", "zh-rHK",
                     "zh-rTW", "en", "en-rUS")
_CJK_LOW = "\u4e00"
_CJK_HIGH = "\u9fff"


def _length8(data, offset):
    first = data[offset]
    if first & 0x80:
        return ((first & 0x7F) << 8) | data[offset + 1], offset + 2
    return first, offset + 1


def _length16(data, offset):
    first = struct.unpack_from("<H", data, offset)[0]
    if first & 0x8000:
        second = struct.unpack_from("<H", data, offset + 2)[0]
        return ((first & 0x7FFF) << 16) | second, offset + 4
    return first, offset + 2


def _parse_pool(data, start):
    chunk_type, header_size, _size = struct.unpack_from("<HHI", data, start)
    if chunk_type != RES_STRING_POOL_TYPE:
        return []
    count, _styles, flags, strings_start, _ss = struct.unpack_from("<IIIII", data, start + 8)
    if count == 0:
        return []
    offsets = struct.unpack_from("<%dI" % count, data, start + header_size)
    base = start + strings_start
    utf8 = bool(flags & UTF8_FLAG)
    out = []
    for offset in offsets:
        cursor = base + offset
        try:
            if utf8:
                _chars, cursor = _length8(data, cursor)
                byte_len, cursor = _length8(data, cursor)
                out.append(data[cursor:cursor + byte_len].decode("utf-8", "replace"))
            else:
                char_len, cursor = _length16(data, cursor)
                out.append(data[cursor:cursor + char_len * 2].decode("utf-16-le", "replace"))
        except (IndexError, struct.error):
            out.append("")
    return out


def _walk(data, start, end, strings):
    cursor = start
    while cursor + 8 <= end:
        chunk_type, header_size, size = struct.unpack_from("<HHI", data, cursor)
        if size < 8 or cursor + size > end:
            break
        if chunk_type == RES_STRING_POOL_TYPE:
            strings.extend(_parse_pool(data, cursor))
        elif chunk_type in (RES_TABLE_TYPE, RES_TABLE_PACKAGE_TYPE):
            _walk(data, cursor + header_size, cursor + size, strings)
        cursor += size


def table_strings(data):
    """Every string in every pool of one resource table."""
    strings = []
    _walk(data, 0, len(data), strings)
    return strings


def package_strings(package_path):
    """Resource strings inside a plugin package (``.plugin`` / ``.apk``)."""
    with zipfile.ZipFile(package_path) as archive:
        try:
            data = archive.read("resources.arsc")
        except KeyError:
            return []
    return table_strings(data)


def chinese_strings(package_path):
    """Sorted unique strings that contain at least one CJK character."""
    seen = []
    known = set()
    for item in package_strings(package_path):
        clean = item.strip()
        if not clean or clean in known:
            continue
        known.add(clean)
        if any(_CJK_LOW <= ch <= _CJK_HIGH for ch in clean):
            seen.append(clean)
    return seen


def _unpack_locale(raw):
    """Two packed language/region bytes -> ``"zh-rCN"`` / ``"default"``."""
    if not raw or raw == b"\x00\x00":
        return "default"
    code = raw.decode("ascii", "replace")
    return code if code.strip("\x00") else "default"


def config_locale(data, offset):
    """Locale of a ``ResTable_config`` at ``offset`` (``"default"`` when unset)."""
    try:
        size = struct.unpack_from("<I", data, offset)[0]
    except struct.error:
        return "default"
    if size < 12:
        return "default"
    lang = _unpack_locale(bytes(data[offset + 8:offset + 10]))
    region = _unpack_locale(bytes(data[offset + 10:offset + 12]))
    if lang == "default":
        return "default"
    if region == "default":
        return lang
    return "%s-r%s" % (lang, region)


def _entries(data, start, end):
    """Yield ``(chunk_type, offset, header_size, size)`` for one chunk's children."""
    cursor = start
    while cursor + 8 <= end:
        chunk_type, header_size, size = struct.unpack_from("<HHI", data, cursor)
        if size < 8 or cursor + size > end:
            break
        yield chunk_type, cursor, header_size, size
        cursor += size


def table_texts(data, prefer=None, keep_all=False):
    """``{resource name: text}`` for string resources, one locale each.

    ``prefer`` is a locale preference list (see :data:`LOCALE_PREFERENCE`);
    the best available locale per resource wins, so a plugin that ships both
    ``zh-rCN`` and ``zh-rTW`` yields Simplified Chinese.
    With ``keep_all`` the result is ``{name: {locale: text}}`` instead.
    """
    order = tuple(prefer or LOCALE_PREFERENCE)
    unknown_rank = len(order)

    values = []
    packages = []
    try:
        _kind, table_header, table_size = struct.unpack_from("<HHI", data, 0)
    except struct.error:
        return {}
    for chunk_type, start, header_size, size in _entries(data, table_header, table_size):
        if chunk_type == RES_STRING_POOL_TYPE and not values:
            values = _parse_pool(data, start)
        elif chunk_type == RES_TABLE_PACKAGE_TYPE:
            packages.append((start, header_size, size))
    if not values:
        return {}

    best = {}          # name -> (rank, text)
    everything = {}    # name -> {locale: text}
    for start, header_size, size in packages:
        try:
            type_off, _lpt, key_off, _lpk = struct.unpack_from("<IIII", data, start + 268)
        except struct.error:
            continue
        types = _parse_pool(data, start + type_off)
        keys = _parse_pool(data, start + key_off)
        for chunk_type, tstart, theader, _tsize in _entries(data, start + header_size,
                                                           start + size):
            if chunk_type != RES_TABLE_TYPE_TYPE:
                continue
            type_id = data[tstart + 8]
            # only the string type carries user-visible text
            if not 0 < type_id <= len(types) or types[type_id - 1] != "string":
                continue
            locale = config_locale(data, tstart + 20)
            rank = order.index(locale) if locale in order else unknown_rank
            try:
                count, entries_start = struct.unpack_from("<II", data, tstart + 12)
            except struct.error:
                continue
            offsets_at = tstart + theader
            for index in range(count):
                try:
                    offset = struct.unpack_from("<I", data, offsets_at + index * 4)[0]
                except struct.error:
                    break
                if offset == 0xFFFFFFFF:
                    continue
                entry = tstart + entries_start + offset
                try:
                    _esize, eflags, key_index = struct.unpack_from("<HHI", data, entry)
                except struct.error:
                    continue
                if eflags & FLAG_COMPLEX:
                    continue
                value_at = entry + 8
                if data[value_at + 3] != TYPE_STRING:
                    continue
                value_index = struct.unpack_from("<I", data, value_at + 4)[0]
                if value_index >= len(values):
                    continue
                name = keys[key_index] if key_index < len(keys) else key_index
                text = values[value_index]
                if keep_all:
                    everything.setdefault(name, {})[locale] = text
                    continue
                current = best.get(name)
                if current is None or rank < current[0]:
                    best[name] = (rank, text)
    if keep_all:
        return everything
    return {name: text for name, (_rank, text) in best.items()}


def package_texts(package_path, prefer=None, keep_all=False):
    """Resource name -> text for one plugin package (``.plugin`` / ``.apk``)."""
    with zipfile.ZipFile(package_path) as archive:
        try:
            data = archive.read("resources.arsc")
        except KeyError:
            return {}
    return table_texts(data, prefer=prefer, keep_all=keep_all)
