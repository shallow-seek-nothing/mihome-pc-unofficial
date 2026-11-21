"""RC4 with the Mi Home keystream warm-up.

Evidence:
  * ``_m_j/ew0.java`` -- textbook RC4 (KSA + PRGA), no private twist.
  * ``_m_j/g7c.java``  -- wraps it; the constructor requires a 32-byte key and
    then encrypts a 1024-byte zero buffer, i.e. both sides discard the first
    1024 bytes of keystream.

The discard must happen on the **same** state object that later encrypts the
payload.  Two independent objects would each restart from the initial S-box and
the server would read garbage.
"""

import base64

BURN_BYTES = 1024

__all__ = ["RC4", "new_stream", "encrypt_b64", "decrypt_b64", "BURN_BYTES"]


class RC4:
    """Standard RC4 stream cipher, resumable."""

    __slots__ = ("_s", "_i", "_j")

    def __init__(self, key):
        key = bytes(bytearray(key))
        if not key:
            key = b"\x00"
        s = list(range(256))
        j = 0
        klen = len(key)
        for i in range(256):
            j = (j + s[i] + key[i % klen]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self._s = s
        self._i = 0
        self._j = 0

    def crypt(self, data):
        """XOR ``data`` with the keystream, advancing the state."""
        s = self._s
        i = self._i
        j = self._j
        out = bytearray(len(data))
        for idx, byte in enumerate(bytearray(data)):
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            out[idx] = byte ^ s[(s[i] + s[j]) & 0xFF]
        self._i = i
        self._j = j
        return bytes(out)

    def skip(self, count):
        """Advance the keystream by ``count`` bytes without producing output."""
        if count <= 0:
            return
        self.crypt(b"\x00" * count)


def new_stream(key):
    """A stream that already burned the 1024-byte warm-up (ready for payload)."""
    stream = RC4(key)
    stream.skip(BURN_BYTES)
    return stream


def encrypt_b64(key, text):
    """Encrypt ``text`` (str) with a fresh burned stream, return base64 str."""
    raw = text.encode("utf-8") if not isinstance(text, bytes) else text
    return base64.b64encode(new_stream(key).crypt(raw)).decode("ascii")


def decrypt_b64(key, payload):
    """Inverse of :func:`encrypt_b64`, returns bytes."""
    raw = base64.b64decode(payload)
    return new_stream(key).crypt(raw)
