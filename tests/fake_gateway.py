"""A fake Mi Home gateway: the server side of the reverse-engineered protocol.

Why this exists
---------------
``CloudClient`` is the one layer that plain unit tests cannot pin: it builds the
signed body, sends it, and decodes the encrypted reply. Every one of those steps
has an ordering/crypto invariant that a self-consistent test could get wrong on
both sides at once. So we implement the *gateway* independently, from the same
reverse-engineering notes, and let the real client talk to it over a real socket.

What the fake gateway enforces (and therefore what a client bug would trip):

* the form body must carry ``signature``/``_nonce``/``ssecurity`` (RC4) or
  ``signature``/``_nonce``/``aes_hash__`` (AES) at the end;
* ``signature`` must equal ``sha1(METHOD&path&k=v sorted&sessionSecurity)`` over
  the **encrypted** values, and ``rc4_hash__`` over the **plain** ones;
* every field must decrypt, and RC4 streams are shared across fields in the
  order they arrive (that is the invariant ``_m_j/wrf.java:668-690`` implies);
* replies are ``base64(RC4(gzip(json)))`` with ``MIOT-CONTENT-ENCODING: GZIP``.
"""
import base64
import gzip
import json
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from mihome_pc.crypto import aes, rc4
from mihome_pc.xiaomi import sign

RC4_ALGORITHM = sign.ALGORITHM_RC4
AES_ALGORITHM = sign.ALGORITHM_AES


class ProtocolViolation(Exception):
    """The client sent something the real gateway would reject."""


class FakeGateway:
    """Threaded HTTP server speaking the Mi Home cloud protocol."""

    def __init__(self, ssecurity, algorithm=RC4_ALGORITHM, gzip_replies=True):
        self.ssecurity = ssecurity                  # base64 str, 32 raw bytes
        self.algorithm = algorithm
        self.gzip_replies = gzip_replies
        self.handlers = {}                          # path -> callable(payload)
        self.requests = []                          # recorded decoded requests
        self.violations = []                        # client bugs seen
        self._server = None
        self._thread = None

    # -- scripting -----------------------------------------------------
    def on(self, path, handler):
        """Register ``handler(payload_dict) -> dict`` for ``path``."""
        self.handlers[path.rstrip("/") or "/"] = handler
        return self

    def static(self, path, result):
        return self.on(path, lambda payload: result)

    # -- lifecycle -----------------------------------------------------
    def start(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "fake-gateway"

            def log_message(self, *args):
                pass

            def do_POST(self):                                 # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8")
                try:
                    status, body, headers = gateway.handle(self.path, raw, self.headers)
                except ProtocolViolation as exc:
                    gateway.violations.append(str(exc))
                    status, body, headers = 400, json.dumps({"error": str(exc)}), {}
                except Exception as exc:                       # noqa: BLE001
                    gateway.violations.append("handler crash: %r" % (exc,))
                    status, body, headers = 500, json.dumps({"error": str(exc)}), {}
                payload = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.daemon = True
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)

    @property
    def base_url(self):
        host, port = self._server.server_address[0], self._server.server_address[1]
        return "http://%s:%d/app" % (host, port)

    # -- protocol ------------------------------------------------------
    def handle(self, path, raw_body, headers):
        pairs = parse_qsl(raw_body, keep_blank_values=True)
        fields = dict(pairs)
        algorithm = headers.get("MIOT-ENCRYPT-ALGORITHM") or RC4_ALGORITHM

        nonce = fields.get("_nonce")
        if not nonce:
            raise ProtocolViolation("missing _nonce")
        session_security = sign.session_security(self.ssecurity, nonce)

        trailing = [name for name, _ in pairs[-3:]]
        signed_pairs = [(k, v) for k, v in pairs
                        if k not in ("signature", "_nonce", "ssecurity", "aes_hash__")]

        if algorithm == AES_ALGORITHM:
            iv_b64 = fields.get("aes_hash__")
            if not iv_b64:
                raise ProtocolViolation("missing aes_hash__ in AES mode")
            if "ssecurity" in fields:
                raise ProtocolViolation("AES mode must not send ssecurity")
            verified = signed_pairs + [("aes_hash__", iv_b64)]
            self._verify_signature(fields, path, verified, session_security)
            payload = self._decrypt_aes(signed_pairs, session_security, iv_b64)
            reply_iv = iv_b64
        else:
            reply_iv = None
            self._verify_signature(fields, path, signed_pairs, session_security)
            if fields.get("ssecurity") != self.ssecurity:
                raise ProtocolViolation("ssecurity echoed back does not match")
            payload = self._decrypt_rc4(signed_pairs, session_security)

        # Only the RC4 mode carries rc4_hash__; the AES mode replaces it with
        # aes_hash__ (already verified through the signature).
        if algorithm == RC4_ALGORITHM:
            if "rc4_hash__" not in payload:
                raise ProtocolViolation("decrypted body has no rc4_hash__")
            expected = sign.signature(
                "POST", path,
                [(k, v) for k, v in payload.items() if k != "rc4_hash__"],
                session_security)
            if payload["rc4_hash__"] != expected:
                raise ProtocolViolation("rc4_hash__ mismatch (plain-params signature)")

        data = payload.get("data")
        if data is None:
            raise ProtocolViolation("no data field")
        try:
            request_payload = json.loads(data)
        except ValueError:
            raise ProtocolViolation("data is not JSON: %r" % data[:80])

        self.requests.append({
            "path": path,
            "payload": request_payload,
            "algorithm": algorithm,
            "trailing": trailing,
            "headers": dict(headers.items()),
        })

        # the real gateway mounts everything under /app; route on the suffix
        route = urlsplit(path).path
        if route.startswith("/app/"):
            route = route[len("/app"):]
        handler = self.handlers.get(route.rstrip("/") or "/")
        if handler is None:
            result = {"code": 404, "message": "no handler for %s" % path}
        else:
            result = handler(request_payload)
            if not isinstance(result, dict) or "code" not in result:
                result = {"code": 0, "result": result}
        return 200, self._encode_reply(result, session_security, reply_iv), {
            "MIOT-CONTENT-ENCODING": "GZIP" if self.gzip_replies else "",
        }

    def _verify_signature(self, fields, path, signed_pairs, session_security):
        received = fields.get("signature")
        if not received:
            raise ProtocolViolation("missing signature")
        expected = sign.signature("POST", urlsplit(path).path, signed_pairs,
                                  session_security)
        if received != expected:
            raise ProtocolViolation(
                "signature mismatch -- client signed a different key order or payload")

    def _decrypt_rc4(self, signed_pairs, session_security):
        stream = rc4.new_stream(base64.b64decode(session_security))
        plain = {}
        for name, value in signed_pairs:
            try:
                plain[name] = stream.crypt(base64.b64decode(value)).decode("utf-8")
            except Exception as exc:                       # noqa: BLE001
                raise ProtocolViolation("field %r does not RC4-decrypt: %r" % (name, exc))
        return plain

    def _decrypt_aes(self, signed_pairs, session_security, iv_b64):
        key = base64.b64decode(session_security)
        iv = base64.b64decode(iv_b64)
        plain = {}
        for name, value in signed_pairs:
            blob = base64.b64decode(value)
            if len(blob) < 16:
                raise ProtocolViolation("field %r too short for GCM" % name)
            try:
                plain[name] = aes.gcm_decrypt(key, iv, blob[:-16], blob[-16:]).decode("utf-8")
            except ValueError as exc:
                raise ProtocolViolation("field %r fails GCM auth: %s" % (name, exc))
        return plain

    def _encode_reply(self, payload, session_security, iv_b64=None):
        raw = json.dumps(payload).encode("utf-8")
        if self.gzip_replies:
            raw = gzip.compress(raw)
        key = base64.b64decode(session_security)
        if self.algorithm == AES_ALGORITHM:
            # The real gateway answers with the same IV it saw (zd.OooOO0 reuses
            # the request's EncryptContext), so echo the client's IV back.
            iv = base64.b64decode(iv_b64 or "")
            ciphertext, tag = aes.gcm_encrypt(key, iv, raw)
            return base64.b64encode(ciphertext + tag).decode("ascii")
        return rc4.encrypt_b64(key, raw)


def ok(result=None):
    return {"code": 0, "result": result if result is not None else {}}


def fail(code, message="error"):
    return {"code": code, "message": message}
