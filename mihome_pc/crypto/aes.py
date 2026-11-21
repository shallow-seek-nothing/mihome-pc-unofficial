"""AES (128/192/256) with CBC and GCM, pure Python, no third-party deps.

Two distinct uses inside the client:

* **Cloud GCM** -- Mi Home 11.x added ``ENCRYPT-AES`` next to ``ENCRYPT-RC4``
  (``_m_j/zd.java``): ``AES/GCM/NoPadding`` with key
  ``sha256(ssecurity || nonce)`` (32 bytes -> AES-256), ``iv = 12 random
  bytes``, 128-bit tag, and ``aes_hash__ = base64(iv)``.
* **LAN miIO** -- AES-128-CBC, ``key = md5(token)``,
  ``iv = md5(key || token)``, PKCS#7 padding.

Written for clarity: payloads are kilobytes at most.
"""

import struct

__all__ = [
    "AES",
    "cbc_encrypt",
    "cbc_decrypt",
    "gcm_encrypt",
    "gcm_decrypt",
    "SBOX",
]

# AES S-box (FIPS-197).  Hard-coded on purpose: it removes any chance of a
# subtle bug in a generated table silently corrupting every request.
SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B,
    0xFE, 0xD7, 0xAB, 0x76, 0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0,
    0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0, 0xB7, 0xFD, 0x93, 0x26,
    0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2,
    0xEB, 0x27, 0xB2, 0x75, 0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0,
    0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84, 0x53, 0xD1, 0x00, 0xED,
    0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F,
    0x50, 0x3C, 0x9F, 0xA8, 0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5,
    0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2, 0xCD, 0x0C, 0x13, 0xEC,
    0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14,
    0xDE, 0x5E, 0x0B, 0xDB, 0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C,
    0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79, 0xE7, 0xC8, 0x37, 0x6D,
    0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F,
    0x4B, 0xBD, 0x8B, 0x8A, 0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E,
    0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E, 0xE1, 0xF8, 0x98, 0x11,
    0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F,
    0xB0, 0x54, 0xBB, 0x16,
)

INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    INV_SBOX[_v] = _i
INV_SBOX = tuple(INV_SBOX)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
         0x6C, 0xD8, 0xAB, 0x4D)


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _gmul(a, b):
    """Multiply in GF(2^8) modulo the AES polynomial x^8+x^4+x^3+x+1."""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


_MUL = {}
for _k in (2, 3, 9, 11, 13, 14):
    _MUL[_k] = tuple(_gmul(i, _k) for i in range(256))


class AES:
    """AES block cipher over 16-byte blocks, 128/192/256-bit keys.

    State layout follows FIPS-197: ``s[4 * column + row]``.
    """

    def __init__(self, key):
        key = bytes(bytearray(key))
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16/24/32 bytes, got %d" % len(key))
        self._nk = len(key) // 4
        self._nr = self._nk + 6
        self._round_keys = self._expand(key)

    # -- key schedule --------------------------------------------------
    def _expand(self, key):
        nk = self._nk
        nr = self._nr
        words = [list(bytearray(key[4 * i:4 * i + 4])) for i in range(nk)]
        for i in range(nk, 4 * (nr + 1)):
            temp = list(words[i - 1])
            if i % nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [SBOX[b] for b in temp]
                temp[0] ^= _RCON[i // nk - 1]
            elif nk > 6 and i % nk == 4:
                temp = [SBOX[b] for b in temp]
            words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
        # concatenate into 16-byte round keys
        return [sum(words[4 * r:4 * r + 4], []) for r in range(nr + 1)]

    # -- primitives ----------------------------------------------------
    @staticmethod
    def _shift_rows(s):
        for r in range(1, 4):
            row = [s[r], s[4 + r], s[8 + r], s[12 + r]]
            row = row[r:] + row[:r]
            s[r], s[4 + r], s[8 + r], s[12 + r] = row

    @staticmethod
    def _inv_shift_rows(s):
        for r in range(1, 4):
            row = [s[r], s[4 + r], s[8 + r], s[12 + r]]
            row = row[-r:] + row[:-r]
            s[r], s[4 + r], s[8 + r], s[12 + r] = row

    @staticmethod
    def _mix_columns(s):
        m2, m3 = _MUL[2], _MUL[3]
        for c in range(4):
            i = 4 * c
            a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
            s[i] = m2[a0] ^ m3[a1] ^ a2 ^ a3
            s[i + 1] = a0 ^ m2[a1] ^ m3[a2] ^ a3
            s[i + 2] = a0 ^ a1 ^ m2[a2] ^ m3[a3]
            s[i + 3] = m3[a0] ^ a1 ^ a2 ^ m2[a3]

    @staticmethod
    def _inv_mix_columns(s):
        m9, m11, m13, m14 = _MUL[9], _MUL[11], _MUL[13], _MUL[14]
        for c in range(4):
            i = 4 * c
            a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
            s[i] = m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3]
            s[i + 1] = m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3]
            s[i + 2] = m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3]
            s[i + 3] = m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]

    def encrypt_block(self, block):
        if len(block) != 16:
            raise ValueError("block must be 16 bytes")
        s = list(bytearray(block))
        rk = self._round_keys
        for i in range(16):
            s[i] ^= rk[0][i]
        for rnd in range(1, self._nr):
            for i in range(16):
                s[i] = SBOX[s[i]]
            self._shift_rows(s)
            self._mix_columns(s)
            key = rk[rnd]
            for i in range(16):
                s[i] ^= key[i]
        for i in range(16):
            s[i] = SBOX[s[i]]
        self._shift_rows(s)
        key = rk[self._nr]
        for i in range(16):
            s[i] ^= key[i]
        return bytes(bytearray(s))

    def decrypt_block(self, block):
        if len(block) != 16:
            raise ValueError("block must be 16 bytes")
        s = list(bytearray(block))
        rk = self._round_keys
        key = rk[self._nr]
        for i in range(16):
            s[i] ^= key[i]
        for rnd in range(self._nr - 1, 0, -1):
            self._inv_shift_rows(s)
            for i in range(16):
                s[i] = INV_SBOX[s[i]]
            key = rk[rnd]
            for i in range(16):
                s[i] ^= key[i]
            self._inv_mix_columns(s)
        self._inv_shift_rows(s)
        for i in range(16):
            s[i] = INV_SBOX[s[i]]
        key = rk[0]
        for i in range(16):
            s[i] ^= key[i]
        return bytes(bytearray(s))


# --- CBC ---------------------------------------------------------------

def _pkcs7_pad(data, block=16):
    pad = block - (len(data) % block)
    return data + bytes(bytearray([pad]) * pad)


def _pkcs7_unpad(data, block=16):
    if not data or len(data) % block:
        raise ValueError("bad padded length")
    pad = data[-1]
    if pad < 1 or pad > block or data[-pad:] != bytes(bytearray([pad]) * pad):
        raise ValueError("bad PKCS#7 padding")
    return data[:-pad]


def cbc_encrypt(key, iv, data):
    """AES-CBC with PKCS#7 padding (LAN miIO)."""
    cipher = AES(key)
    data = _pkcs7_pad(bytes(bytearray(data)))
    prev = bytes(bytearray(iv))
    out = bytearray()
    for i in range(0, len(data), 16):
        block = bytes(bytearray(a ^ b for a, b in zip(
            bytearray(data[i:i + 16]), bytearray(prev))))
        prev = cipher.encrypt_block(block)
        out += prev
    return bytes(out)


def cbc_decrypt(key, iv, data):
    """AES-CBC decryption with PKCS#7 unpadding (LAN miIO)."""
    cipher = AES(key)
    data = bytes(bytearray(data))
    if len(data) % 16:
        raise ValueError("ciphertext length must be a multiple of 16")
    prev = bytes(bytearray(iv))
    out = bytearray()
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        dec = cipher.decrypt_block(block)
        out += bytes(bytearray(a ^ b for a, b in zip(bytearray(dec), bytearray(prev))))
        prev = block
    return _pkcs7_unpad(bytes(out))


# --- GCM ---------------------------------------------------------------

_R = 0xE1000000000000000000000000000000


def _gf_mul(x, y):
    """Multiplication in GF(2^128) as specified by GCM (SP 800-38D 6.3)."""
    xb = bytearray(x)
    v = int.from_bytes(bytes(bytearray(y)), "big")
    z = bytearray(16)
    for i in range(128):
        if (xb[i >> 3] >> (7 - (i & 7))) & 1:
            z = bytearray(zi ^ vi for zi, vi in zip(z, v.to_bytes(16, "big")))
        if v & 1:
            v = (v >> 1) ^ _R
        else:
            v >>= 1
    return bytes(z)


def _ghash(h, aad, ciphertext):
    def xor(a, b):
        return bytes(bytearray(x ^ y for x, y in zip(bytearray(a), bytearray(b))))

    def pad16(data):
        rem = len(data) % 16
        return data if not rem else data + b"\x00" * (16 - rem)

    y = b"\x00" * 16
    padded_aad = pad16(aad)
    for i in range(0, len(padded_aad), 16):
        y = _gf_mul(xor(y, padded_aad[i:i + 16]), h)
    padded_ct = pad16(ciphertext)
    for i in range(0, len(padded_ct), 16):
        y = _gf_mul(xor(y, padded_ct[i:i + 16]), h)
    lens = struct.pack(">QQ", len(aad) * 8, len(ciphertext) * 8)
    return _gf_mul(xor(y, lens), h)


def _inc32(block):
    prefix = bytes(bytearray(block[:12]))
    ctr = (struct.unpack(">I", bytes(bytearray(block[12:16])))[0] + 1) & 0xFFFFFFFF
    return prefix + struct.pack(">I", ctr)


def _gctr(cipher, icb, data):
    prefix = bytes(bytearray(icb[:12]))
    ctr = struct.unpack(">I", bytes(bytearray(icb[12:16])))[0]
    out = bytearray()
    for i in range(0, len(data), 16):
        ks = cipher.encrypt_block(prefix + struct.pack(">I", ctr & 0xFFFFFFFF))
        chunk = data[i:i + 16]
        out += bytes(bytearray(a ^ b for a, b in zip(bytearray(chunk), bytearray(ks))))
        ctr = (ctr + 1) & 0xFFFFFFFF
    return bytes(out)


def _j0(cipher, iv):
    if len(iv) == 12:
        return bytes(bytearray(iv)) + b"\x00\x00\x00\x01"
    # general case: GHASH over padded IV and its bit length
    h = cipher.encrypt_block(b"\x00" * 16)
    rem = len(iv) % 16
    padded = bytes(bytearray(iv)) + (b"\x00" * (16 - rem) if rem else b"")
    y = _gf_mul(_ghash_raw(h, padded + b"\x00" * 8 + struct.pack(">Q", len(iv) * 8)), h)
    return y


def _ghash_raw(h, data):
    y = b"\x00" * 16
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        y = _gf_mul(bytes(bytearray(a ^ b for a, b in zip(bytearray(y), bytearray(block)))), h)
    return y


def gcm_encrypt(key, iv, plaintext, aad=b""):
    """AES-GCM encryption. Returns ``(ciphertext, tag)``."""
    cipher = AES(key)
    j0 = _j0(cipher, iv)
    ct = _gctr(cipher, _inc32(j0), bytes(bytearray(plaintext)))
    h = cipher.encrypt_block(b"\x00" * 16)
    s = _ghash(h, bytes(bytearray(aad)), ct)
    # T = AES_K(J0) XOR S   (NOT AES_K(J0 XOR S))
    tag = bytes(bytearray(
        a ^ b for a, b in zip(bytearray(cipher.encrypt_block(j0)), bytearray(s))))
    return ct, tag


def gcm_decrypt(key, iv, ciphertext, tag, aad=b""):
    """AES-GCM decryption; raises ``ValueError`` when the tag does not verify."""
    cipher = AES(key)
    j0 = _j0(cipher, iv)
    h = cipher.encrypt_block(b"\x00" * 16)
    s = _ghash(h, bytes(bytearray(aad)), bytes(bytearray(ciphertext)))
    expected = bytes(bytearray(
        a ^ b for a, b in zip(bytearray(cipher.encrypt_block(j0)), bytearray(s))))
    if expected != bytes(bytearray(tag)):
        raise ValueError("GCM tag mismatch")
    return _gctr(cipher, _inc32(j0), bytes(bytearray(ciphertext)))
