"""Xiaomi cloud signing, request encoding and response decoding.

Everything here is a pure function over bytes/str so it can be pinned with
recorded fixtures and needs no network.

Reverse-engineered from Mi Home 11.9.705:

* nonce        ``_m_j/ys2.java:22``  -- 8 random bytes + **4-byte BE int** of
  minutes ``(now + server_offset) / 60000``, base64 (no wrap).
* sessionSecurity ``_m_j/wrf.java:645`` -- ``base64(sha256(b64d(ssecurity) ||
  b64d(nonce)))``.
* signature    ``_m_j/ys2.java:34``  -- ``base64(sha1(join("&", [
  METHOD, encoded_path, "k=v" sorted by key, sessionSecurity])))``.
* RC4 body     ``_m_j/wrf.java:665`` -- ``rc4_hash__`` signed over the *plain*
  params, then every value is RC4-encrypted (one shared 1024-burned stream), then
  ``signature`` signed over the **encrypted** map, then ``_nonce``/``ssecurity``.
* AES body     ``_m_j/zd.java:115``  -- 12-byte IV, values AES-256-GCM
  encrypted, ``aes_hash__ = base64(iv)``, signature over the encrypted map plus
  ``aes_hash__``, ``_nonce``; **no** ``ssecurity`` field.

The invariant that actually matters on the wire: the key order used for signing
must equal the order the fields are submitted in.  The app uses a ``TreeMap``,
i.e. ascending key order, so we do the same.
"""

import base64
import gzip
import hashlib
import json
import os
import time

from urllib.parse import quote, urlsplit

from ..crypto import aes, rc4

ALGORITHM_RC4 = "ENCRYPT-RC4"
ALGORITHM_AES = "ENCRYPT-AES"

__all__ = [
    "gen_nonce",
    "session_security",
    "signature",
    "api_path",
    "encode_rc4",
    "encode_aes",
    "decode_response",
    "decode_response_aes",
    "ALGORITHM_RC4",
    "ALGORITHM_AES",
]


class SignError(Exception):
    """Raised when a response cannot be decoded."""


# --------------------------------------------------------------------------
# nonce / session security
# --------------------------------------------------------------------------

def gen_nonce(now_ms=None, server_offset_ms=0):
    """``base64(8 random bytes + int32 BE minutes)``."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    minutes = int((now_ms + server_offset_ms) / 60000)
    raw = _random_long_bytes() + _int32_be(minutes)
    return base64.b64encode(raw).decode("ascii")


def _random_long_bytes():
    """8 random bytes, same shape as Java's ``new Random().nextLong()``."""
    return os.urandom(8)


def _int32_be(value):
    value &= 0xFFFFFFFF
    return bytes(bytearray([(value >> 24) & 0xFF, (value >> 16) & 0xFF,
                            (value >> 8) & 0xFF, value & 0xFF]))


def session_security(ssecurity_b64, nonce_b64):
    """``base64(sha256(b64decode(ssecurity) + b64decode(nonce)))``."""
    digest = hashlib.sha256()
    digest.update(base64.b64decode(ssecurity_b64))
    digest.update(base64.b64decode(nonce_b64))
    return base64.b64encode(digest.digest()).decode("ascii")


# --------------------------------------------------------------------------
# signature
# --------------------------------------------------------------------------

def api_path(url_or_path):
    """``https://api.mijia.tech/app/miotspec/prop/get`` -> ``/miotspec/prop/get``.

    The app signs ``Uri.parse(url).getEncodedPath()``, i.e. the path *without*
    the ``/app`` prefix it was routed under.  Our callers pass the untrimmed
    path, so trim it here.
    """
    if "://" in url_or_path:
        path = urlsplit(url_or_path).path
    else:
        path = url_or_path
    if path.startswith("/app/"):
        path = path[len("/app"):]
    elif path == "/app":
        path = "/"
    # getEncodedPath() keeps the percent-encoding; re-encode anything unsafe
    return quote(path, safe="/~!$&'()*+,;=:@-._")


def signature(method, url_or_path, params_pairs, session_security_b64):
    """SHA1 signature over the given pairs, **sorted by key**.

    ``params_pairs`` may be a dict or a sequence of ``(key, value)`` pairs.
    """
    if not session_security_b64:
        raise SignError("sessionSecurity is null")
    parts = []
    if method:
        parts.append(str(method).upper())
    if url_or_path:
        parts.append(api_path(url_or_path))
    items = params_pairs.items() if hasattr(params_pairs, "items") else params_pairs
    for key, value in sorted(items, key=lambda kv: _java_string_key(kv[0])):
        parts.append("%s=%s" % (key, value))
    parts.append(session_security_b64)
    text = "&".join(parts)
    digest = hashlib.sha1(text.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _to_bytes(value):
    """Form values may legitimately be ints (e.g. ``datasource: 1``)."""
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        value = str(value)
    return value.encode("utf-8")


def _java_string_key(value):
    """Java ``TreeMap`` compares UTF-16 code units, not code points."""
    try:
        return value.encode("utf-16-be")
    except AttributeError:
        return str(value).encode("utf-16-be")


# --------------------------------------------------------------------------
# request body: RC4
# --------------------------------------------------------------------------

def encode_rc4(url, method, params, ssecurity_b64, nonce=None,
               now_ms=None, server_offset_ms=0):
    """Build the RC4-encrypted form body.

    Returns ``(pairs, context)`` where ``pairs`` is an ordered list of
    ``(key, value)`` ready to be urlencoded, and ``context`` holds the nonce
    and session security needed to decode the response.
    """
    if nonce is None:
        nonce = gen_nonce(now_ms, server_offset_ms)
    ss = session_security(ssecurity_b64, nonce)

    plain = list(params.items()) if hasattr(params, "items") else list(params)
    rc4_hash = signature(method, url, plain, ss)
    plain.append(("rc4_hash__", rc4_hash))

    # One shared stream for all values, in the same (sorted) order they are
    # submitted -- that is what the app does (wrf.java:668-690).
    plain_sorted = sorted(plain, key=lambda kv: _java_string_key(kv[0]))
    stream = rc4.new_stream(base64.b64decode(ss))
    encrypted = []
    for key, value in plain_sorted:
        raw = _to_bytes(value)
        encrypted.append((key, base64.b64encode(stream.crypt(raw)).decode("ascii")))

    body = list(encrypted)
    body.append(("signature", signature(method, url, encrypted, ss)))
    body.append(("_nonce", nonce))
    body.append(("ssecurity", ssecurity_b64))
    return body, {"algorithm": ALGORITHM_RC4, "nonce": nonce,
                  "session_security": ss}


# --------------------------------------------------------------------------
# request body: AES-GCM
# --------------------------------------------------------------------------

def encode_aes(url, method, params, ssecurity_b64, nonce=None,
               now_ms=None, server_offset_ms=0, iv=None):
    """Build the AES-GCM (``ENCRYPT-AES``) form body."""
    if nonce is None:
        nonce = gen_nonce(now_ms, server_offset_ms)
    ss = session_security(ssecurity_b64, nonce)
    key = base64.b64decode(ss)
    if iv is None:
        iv = os.urandom(12)

    pairs = list(params.items()) if hasattr(params, "items") else list(params)
    encrypted = []
    for key_name, value in sorted(pairs, key=lambda kv: _java_string_key(kv[0])):
        ct, tag = aes.gcm_encrypt(key, iv, _to_bytes(value))
        encrypted.append((key_name, base64.b64encode(ct + tag).decode("ascii")))

    iv_b64 = base64.b64encode(iv).decode("ascii")
    signed = list(encrypted)
    signed.append(("aes_hash__", iv_b64))

    body = list(encrypted)
    body.append(("aes_hash__", iv_b64))
    body.append(("signature", signature(method, url, signed, ss)))
    body.append(("_nonce", nonce))
    return body, {"algorithm": ALGORITHM_AES, "nonce": nonce,
                  "session_security": ss, "iv": iv}


# --------------------------------------------------------------------------
# response decoding
# --------------------------------------------------------------------------

def decode_response(session_security_b64, text, content_encoding=None):
    """Decode a gateway response.

    Two shapes exist in the wild:
      * already-plain JSON (error paths, some endpoints)
      * ``base64(RC4(gzip(json)))``

    ``content_encoding`` is the ``MIOT-CONTENT-ENCODING`` header the app checks;
    when absent we fall back to sniffing the gzip magic.
    """
    if text is None:
        raise SignError("empty response")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except ValueError as exc:
            raise SignError("response is not valid JSON: %s" % exc)

    try:
        raw = rc4.decrypt_b64(base64.b64decode(session_security_b64), stripped)
    except Exception as exc:                       # noqa: BLE001 - report clearly
        raise SignError("cannot base64/RC4 decode response: %s" % exc)

    raw = _maybe_gunzip(raw, content_encoding)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise SignError("decoded response is not JSON: %s" % exc)


def decode_response_aes(session_security_b64, text, iv_b64, content_encoding=None):
    """Decode an ``ENCRYPT-AES`` response."""
    stripped = text.lstrip() if text else ""
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    key = base64.b64decode(session_security_b64)
    iv = base64.b64decode(iv_b64)
    blob = base64.b64decode(stripped)
    if len(blob) < 16:
        raise SignError("AES response too short")
    ct, tag = blob[:-16], blob[-16:]
    try:
        raw = aes.gcm_decrypt(key, iv, ct, tag)
    except ValueError as exc:
        raise SignError("AES-GCM decode failed: %s" % exc)
    raw = _maybe_gunzip(raw, content_encoding)
    return json.loads(raw.decode("utf-8", "replace"))


def _maybe_gunzip(raw, content_encoding):
    wants_gzip = (content_encoding or "").upper() == "GZIP" or raw[:2] == b"\x1f\x8b"
    if not wants_gzip:
        return raw
    try:
        return gzip.decompress(raw)
    except OSError as exc:
        raise SignError("gzip decompress failed: %s" % exc)
