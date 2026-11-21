"""Which dex methods reference a string literal?

Needed when jadx shows no hit but ``grep -c`` on ``classes.dex`` finds the
bytes: the literal may only live in the string table (dead), or be used by a
method jadx could not decompile.  This walks ``class_def_item -> code_item``
and looks for ``const-string`` / ``const-string/jumbo`` with that index.

Usage:
    python tools/dex_literal_refs.py <classes.dex> <literal>
"""
import struct
import sys

CONST_STRING = 0x1A
CONST_STRING_JUMBO = 0x1B


def _uleb(data, offset):
    result = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7


def _string_at(data, offset):
    """DEX string_data_item: uleb128 utf16 length, then MUTF-8 bytes."""
    _length, offset = _uleb(data, offset)
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8", "replace")


def literal_index(data, needle):
    """Index in the dex string table, or ``None``."""
    count, base = struct.unpack_from("<II", data, 56)
    for index in range(count):
        offset = struct.unpack_from("<I", data, base + index * 4)[0]
        if _string_at(data, offset) == needle:
            return index
    return None


def type_names(data):
    string_count, string_base = struct.unpack_from("<II", data, 56)
    type_count, type_base = struct.unpack_from("<II", data, 64)
    names = []
    for index in range(type_count):
        string_index = struct.unpack_from("<I", data, type_base + index * 4)[0]
        offset = struct.unpack_from("<I", data, string_base + string_index * 4)[0]
        names.append(_string_at(data, offset))
    return names


def method_names(data, types):
    string_count, string_base = struct.unpack_from("<II", data, 56)
    method_count, method_base = struct.unpack_from("<II", data, 88)
    names = []
    for index in range(method_count):
        base = method_base + index * 8
        class_index, _proto, name_index = struct.unpack_from("<HHI", data, base)
        offset = struct.unpack_from("<I", data, string_base + name_index * 4)[0]
        names.append((types[class_index], _string_at(data, offset)))
    return names


def references(data, target):
    """Method indices whose code contains a matching const-string."""
    class_count, class_base = struct.unpack_from("<II", data, 96)
    hits = set()
    for index in range(class_count):
        base = class_base + index * 32
        class_data = struct.unpack_from("<I", data, base + 24)[0]
        if not class_data:
            continue
        offset = class_data
        static_n, offset = _uleb(data, offset)
        instance_n, offset = _uleb(data, offset)
        direct_n, offset = _uleb(data, offset)
        virtual_n, offset = _uleb(data, offset)
        for _ in range(static_n + instance_n):
            _field, offset = _uleb(data, offset)
            _flags, offset = _uleb(data, offset)
        method_index = 0
        for _ in range(direct_n + virtual_n):
            diff, offset = _uleb(data, offset)
            _flags, offset = _uleb(data, offset)
            code_off, offset = _uleb(data, offset)
            method_index += diff
            if not code_off:
                continue
            insns_size = struct.unpack_from("<I", data, code_off + 12)[0]
            units = struct.unpack_from("<%dH" % insns_size, data, code_off + 16)
            for position, unit in enumerate(units):
                opcode = unit & 0xFF
                if opcode == CONST_STRING and position + 1 < len(units) \
                        and units[position + 1] == target:
                    hits.add(method_index)
                    break
                if opcode == CONST_STRING_JUMBO and position + 2 < len(units):
                    wide = units[position + 1] | (units[position + 2] << 16)
                    if wide == target:
                        hits.add(method_index)
                        break
    return hits


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    with open(argv[1], "rb") as handle:
        data = handle.read()
    target = literal_index(data, argv[2])
    if target is None:
        print("literal not in the string table of this dex")
        return 1
    types = type_names(data)
    names = method_names(data, types)
    hits = references(data, target)
    print("string index %d, referencing methods: %d" % (target, len(hits)))
    for method_index in sorted(hits):
        owner, name = names[method_index]
        print("  %s#%s" % (owner, name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
