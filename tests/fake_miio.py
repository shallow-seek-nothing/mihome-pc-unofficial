"""A fake miIO device: the server side of the UDP 54321 protocol.

Same reasoning as ``fake_gateway.py``: the checksum, the AES key derivation and
the header layout are invariants that a self-consistent client test cannot pin.
This responder is written from the protocol notes independently, runs on a real
UDP socket, and can reproduce the awkward behaviours we have actually met:

* ``drop_first=N`` -- Wi-Fi power-save devices swallow the first N packets.
* ``stamp_error_once`` -- a stale handshake, which the client must repair by
  re-handshaking instead of failing.
* ``result_as_string`` -- a few firmwares answer ``get_properties`` with a JSON
  *string* rather than an array.
"""
import hashlib
import json
import socket
import struct
import threading

from mihome_pc.crypto import aes
import contextlib

MAGIC = b"\x21\x31"
HELLO = b"\x21\x31\x00\x20" + b"\xff" * 28
HEADER = struct.Struct(">HHIII")


class FakeMiioDevice:
    """Threaded UDP responder speaking miIO."""

    def __init__(self, token, did=1234, stamp=0x11223344, model="xiaomi.dishwasher.s2",
                 values=None, drop_first=0, stamp_error_once=False,
                 result_as_string=False, bad_checksum=False):
        self.token = token if isinstance(token, bytes) else bytes.fromhex(token)
        self.key = hashlib.md5(self.token).digest()
        self.iv = hashlib.md5(self.key + self.token).digest()
        self.did = did
        self.stamp = stamp
        self.model = model
        self.values = dict(values or {})
        self.drop_first = drop_first
        self.stamp_error_once = stamp_error_once
        self.result_as_string = result_as_string
        self.bad_checksum = bad_checksum

        self.requests = []          # decoded commands seen
        self.violations = []        # things a real device would reject
        self.handshakes = 0
        self._dropped = 0
        self._stamp_error_sent = False
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle -----------------------------------------------------
    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve)
        self._thread.daemon = True
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()

    @property
    def port(self):
        return self._sock.getsockname()[1]

    # -- server loop ---------------------------------------------------
    def _serve(self):
        while not self._stop.is_set():
            try:
                data, peer = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                reply = self.handle(data)
            except Exception as exc:                       # noqa: BLE001
                self.violations.append("handler crash: %r" % (exc,))
                reply = None
            if reply:
                with contextlib.suppress(OSError):
                    self._sock.sendto(reply, peer)

    # -- protocol ------------------------------------------------------
    def handle(self, data):
        if data == HELLO:
            self.handshakes += 1
            return self._packet(b"", payload_already_padded=True, empty=True)
        if len(data) < 32:
            self.violations.append("short packet (%d bytes)" % len(data))
            return None
        if data[:2] != MAGIC:
            self.violations.append("bad magic %r" % data[:2])
            return None

        length = struct.unpack(">H", data[2:4])[0]
        if length != len(data):
            self.violations.append("length field %d != datagram %d" % (length, len(data)))
        expected = hashlib.md5(data[:16] + self.token + data[32:]).digest()
        if data[16:32] != expected:
            self.violations.append("checksum mismatch")
            return None

        header = HEADER.unpack(data[:16])
        _magic, _length, _unknown, did, stamp = header
        if did != self.did or stamp != self.stamp:
            self.violations.append("stale did/stamp %r (expected %r)"
                                   % ((did, stamp), (self.did, self.stamp)))
            return self._error_packet(-9999, "invalid_stamp")

        plain = aes.cbc_decrypt(self.key, self.iv, data[32:length])
        try:
            request = json.loads(plain.rstrip(b"\x00").decode("utf-8"))
        except ValueError:
            self.violations.append("payload is not JSON")
            return None

        if self._dropped < self.drop_first:
            self._dropped += 1
            return None                                  # simulate power save

        self.requests.append(request)
        if self.stamp_error_once and not self._stamp_error_sent:
            self._stamp_error_sent = True
            return self._error_packet(-9999, "invalid_stamp")

        return self._reply(request)

    def _reply(self, request):
        method = request.get("method")
        msg_id = request.get("id")
        params = request.get("params") or []

        if method == "miIO.info":
            result = {"model": self.model, "did": str(self.did),
                      "mac": "AA:BB:CC:DD:EE:FF", "fw_ver": "1.0.0"}
        elif method == "get_properties":
            items = []
            for param in params:
                key = (param.get("siid"), param.get("piid"))
                items.append({"did": str(self.did), "siid": param.get("siid"),
                              "piid": param.get("piid"), "code": 0,
                              "value": self.values.get(key)})
            result = items
        elif method == "set_properties":
            items = []
            for param in params:
                key = (param.get("siid"), param.get("piid"))
                self.values[key] = param.get("value")
                items.append({"did": str(self.did), "siid": param.get("siid"),
                              "piid": param.get("piid"), "code": 0})
            result = items
        elif method == "action":
            result = [{"did": str(self.did), "siid": params[0].get("siid"),
                       "aiid": params[0].get("aiid"), "code": 0}]
        else:
            return self._error_packet(-1, "unsupported method %s" % method)

        if self.result_as_string:
            result = json.dumps(result, separators=(",", ":"))

        return self._packet(json.dumps({"id": msg_id, "result": result},
                                       separators=(",", ":")).encode("utf-8"))

    def _error_packet(self, code, message):
        body = json.dumps({"id": 1, "error": {"code": code, "message": message}},
                          separators=(",", ":")).encode("utf-8")
        return self._packet(body)

    def _packet(self, payload, payload_already_padded=False, empty=False):
        if empty:
            header = HEADER.pack(0x2131, 32, 0, self.did, self.stamp)
            checksum = hashlib.md5(header + self.token).digest()
            return header + checksum
        encrypted = aes.cbc_encrypt(self.key, self.iv, payload + b"\x00")
        header = HEADER.pack(0x2131, 32 + len(encrypted), 0, self.did, self.stamp)
        checksum = hashlib.md5(header + self.token + encrypted).digest()
        if self.bad_checksum:
            checksum = b"\x00" * 16
        return header + checksum + encrypted

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
