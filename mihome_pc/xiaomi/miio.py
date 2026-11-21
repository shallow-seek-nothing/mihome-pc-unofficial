"""Local miIO client -- the fast path over UDP 54321.

A device that is on the same LAN and whose token we know answers directly,
without the cloud round-trip (v2 measured ~50 ms versus ~400 ms through the
gateway).  The token comes from the account's device list.

Wire format (confirmed against the working v2 client and re-checked here)::

    packet   = header(16) + checksum(16) + ciphertext
    header   = >HHIII: magic 0x2131, total length, 0, did, stamp
    checksum = md5(header + token + ciphertext)      # header AND token AND body
    cipher   = AES-128-CBC(key=md5(token), iv=md5(key + token), PKCS#7)
    plain    = json + b"\\x00"

Handshake: send ``21 31 00 20`` followed by 28 ``0xFF`` bytes; the reply's
header carries the ``did`` and ``stamp`` that every later packet must echo.

Two field-tested quirks are encoded here:

* Wi-Fi power-save devices drop the first couple of packets, so we keep **one
  socket** and retry on the same socket rather than reconnecting.
* Some firmware answers ``get_properties`` with a JSON *string* instead of an
  array; that is normalised rather than treated as an error.
"""
import hashlib
import json
import socket
import struct
import time

from ..crypto import aes
import contextlib

__all__ = ["MiioDevice", "MiioError", "probe", "MAGIC", "HELLO", "PORT",
           "token_key_iv"]

MAGIC = b"\x21\x31"
HELLO = b"\x21\x31\x00\x20" + b"\xff" * 28
PORT = 54321
HEADER = struct.Struct(">HHIII")
HEADER_SIZE = 32          # header + checksum


class MiioError(Exception):
    """Device unreachable, refused, or answered something unusable."""


def token_key_iv(token):
    """``token`` (16 bytes or hex str) -> (key, iv) for AES-128-CBC."""
    token = _as_bytes(token)
    if len(token) != 16:
        raise MiioError("miIO token must be 16 bytes, got %d" % len(token))
    key = hashlib.md5(token).digest()
    iv = hashlib.md5(key + token).digest()
    return key, iv


def _as_bytes(token):
    if isinstance(token, (bytes, bytearray)):
        return bytes(bytearray(token))
    if isinstance(token, str):
        return bytes.fromhex(token.strip())
    raise MiioError("token must be bytes or a hex string")


class MiioDevice:
    """One device, one socket. Use as a context manager."""

    def __init__(self, ip, token, port=PORT, timeout=3.0, retries=3,
                 sock=None):
        self.ip = ip
        self.port = port
        self.token = _as_bytes(token)
        self.key, self.iv = token_key_iv(self.token)
        self.timeout = timeout
        self.retries = retries
        self.did = None
        self.stamp = None
        self.model = None
        self._msg_id = 0
        self._sock = sock
        self._own_socket = sock is None
        if self._sock is None:
            self._open()
        try:
            self.handshake()
        except Exception:
            self.close()
            raise

    # -- socket --------------------------------------------------------
    def _open(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.settimeout(0.25)

    def close(self):
        if self._sock is not None and self._own_socket:
            with contextlib.suppress(OSError):
                self._sock.close()
        self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _recv_until(self, seconds):
        """Read datagrams until a miIO one arrives or the window closes."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                chunk, peer = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return None
            if chunk[:2] == MAGIC:
                return chunk, peer
        return None

    # -- framing -------------------------------------------------------
    def handshake(self, tries=6):
        data = None
        for _ in range(tries):
            try:
                self._sock.sendto(HELLO, (self.ip, self.port))
            except OSError:
                continue
            got = self._recv_until(0.5)
            if got:
                data = got[0]
                break
        if data is None or len(data) < HEADER_SIZE:
            raise MiioError("no hello reply from %s (asleep or off-LAN)" % self.ip)
        _magic, _length, _unknown, did, stamp = HEADER.unpack(data[:16])
        self.did, self.stamp = did, stamp
        return did, stamp

    def _encode(self, payload):
        encrypted = aes.cbc_encrypt(self.key, self.iv, payload + b"\x00")
        header = HEADER.pack(0x2131, HEADER_SIZE + len(encrypted), 0,
                             self.did, self.stamp)
        checksum = hashlib.md5(header + self.token + encrypted).digest()
        return header + checksum + encrypted

    def _decode(self, data):
        length = struct.unpack(">H", data[2:4])[0]
        if length < HEADER_SIZE or length > len(data):
            length = len(data)
        expected = hashlib.md5(data[:16] + self.token + data[32:length]).digest()
        if data[16:32] != expected:
            raise MiioError("checksum mismatch from %s" % self.ip)
        encrypted = data[32:length]
        if len(encrypted) < 16:
            return None
        plain = aes.cbc_decrypt(self.key, self.iv, encrypted)
        try:
            return json.loads(plain.rstrip(b"\x00").decode("utf-8", "replace"))
        except ValueError:
            return None

    def call(self, method, params=None, timeout=None):
        """Send one command and return the decoded reply dict."""
        self._msg_id += 1
        body = json.dumps({"id": self._msg_id, "method": method,
                           "params": params if params is not None else []},
                          separators=(",", ":")).encode("utf-8")
        packet = self._encode(body)
        last_error = None
        for _attempt in range(self.retries + 1):
            try:
                self._sock.sendto(packet, (self.ip, self.port))
            except OSError as exc:
                last_error = MiioError("send to %s failed: %s" % (self.ip, exc))
                continue
            got = self._recv_until(timeout or self.timeout)
            if not got:
                last_error = MiioError("%s timed out on %s" % (method, self.ip))
                continue
            try:
                response = self._decode(got[0])
            except MiioError as exc:
                last_error = exc
                continue
            if response is None:
                continue
            if "error" in response:
                error = response["error"] or {}
                message = str(error.get("message", error))
                # a stale stamp means our handshake aged out: redo it once
                if "stamp" in message.lower() or error.get("code") == -9999:
                    self.handshake()
                    packet = self._encode(body)
                    continue
                raise MiioError("device refused %s: %s" % (method, error))
            return response
        raise last_error or MiioError("%s failed on %s" % (method, self.ip))

    # -- API -----------------------------------------------------------
    @staticmethod
    def _result(response, method):
        result = response.get("result")
        if isinstance(result, str):
            # some models (pet water dispenser) answer with a JSON string
            try:
                result = json.loads(result)
            except ValueError:
                raise MiioError("%s: unusable result %r" % (method, result[:60]))
        if not isinstance(result, list):
            raise MiioError("%s: unexpected result %s"
                            % (method, type(result).__name__))
        return result

    def info(self):
        result = self.call("miIO.info", [])
        payload = result.get("result")
        if isinstance(payload, dict):
            self.model = payload.get("model") or self.model
            return payload
        raise MiioError("miIO.info returned %r" % (payload,))

    def get_properties(self, pairs, timeout=None):
        """``[(siid, piid)]`` -> ``{(siid, piid): value}``."""
        params = [{"did": str(self.did), "siid": siid, "piid": piid}
                  for siid, piid in pairs]
        result = self._result(self.call("get_properties", params, timeout), "get_properties")
        return {(item.get("siid"), item.get("piid")): item.get("value")
                    for item in result}

    def set_properties(self, triples, timeout=None):
        """``[(siid, piid, value)]`` -> the device's result list."""
        params = [{"did": str(self.did), "siid": siid, "piid": piid, "value": value}
                  for siid, piid, value in triples]
        return self._result(self.call("set_properties", params, timeout), "set_properties")

    def action(self, siid, aiid, args=None):
        params = {"did": str(self.did), "siid": siid, "aiid": aiid}
        if args:
            params["in"] = args
        return self._result(self.call("action", [params]), "action")


def probe(ip, token, timeout=1.0, port=PORT):
    """Reachability check: returns the model string, or ``None``."""
    try:
        with MiioDevice(ip, token, timeout=timeout, retries=1, port=port) as device:
            return (device.info() or {}).get("model")
    except (MiioError, OSError):
        return None
