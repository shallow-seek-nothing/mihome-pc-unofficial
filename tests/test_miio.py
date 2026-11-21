"""miIO (LAN) tests: the real client against a fake UDP device."""
import hashlib
import os
import socket
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_miio                                     # noqa: E402
from mihome_pc.xiaomi import miio                     # noqa: E402

TOKEN = "00112233445566778899aabbccddeeff"
TOKEN_BYTES = bytes.fromhex(TOKEN)


class MiioCase(unittest.TestCase):
    """Each test gets its own fake device on its own UDP port."""

    device_kwargs = {}

    def setUp(self):
        self.fake = fake_miio.FakeMiioDevice(TOKEN_BYTES, **self.device_kwargs).start()
        self.addCleanup(self.fake.stop)
        self.addCleanup(self._check_violations)

    def _check_violations(self):
        if self.fake.violations:
            self.fail("client violated the LAN protocol: %s" % self.fake.violations)

    def open_device(self, **kwargs):
        kwargs.setdefault("timeout", 0.6)
        kwargs.setdefault("retries", 2)
        return miio.MiioDevice("127.0.0.1", TOKEN, port=self.fake.port, **kwargs)


class TestHandshakeAndInfo(MiioCase):
    def test_handshake_reads_did_and_stamp(self):
        device = self.open_device()
        self.addCleanup(device.close)
        self.assertEqual(device.did, self.fake.did)
        self.assertEqual(device.stamp, self.fake.stamp)
        self.assertEqual(self.fake.handshakes, 1)

    def test_info_returns_model(self):
        device = self.open_device()
        self.addCleanup(device.close)
        info = device.info()
        self.assertEqual(info["model"], self.fake.model)
        self.assertEqual(device.model, self.fake.model)

    def test_hello_packet_shape(self):
        self.assertEqual(miio.HELLO[:4], b"\x21\x31\x00\x20")
        self.assertEqual(len(miio.HELLO), 32)
        self.assertEqual(miio.HELLO[4:], b"\xff" * 28)


class TestProperties(MiioCase):
    device_kwargs = {"values": {(2, 1): True, (2, 4): 3}}

    def test_get_properties(self):
        device = self.open_device()
        self.addCleanup(device.close)
        values = device.get_properties([(2, 1), (2, 4)])
        self.assertEqual(values, {(2, 1): True, (2, 4): 3})
        sent = self.fake.requests[-1]
        self.assertEqual(sent["method"], "get_properties")
        self.assertEqual(sent["params"][0],
                         {"did": str(self.fake.did), "siid": 2, "piid": 1})

    def test_set_properties(self):
        device = self.open_device()
        self.addCleanup(device.close)
        result = device.set_properties([(2, 4, 5)])
        self.assertEqual(result[0]["code"], 0)
        sent = self.fake.requests[-1]
        self.assertEqual(sent["params"][0]["value"], 5)
        self.assertEqual(self.fake.values[(2, 4)], 5)

    def test_action(self):
        device = self.open_device()
        self.addCleanup(device.close)
        result = device.action(2, 1, ["arg"])
        self.assertEqual(result[0]["code"], 0)
        self.assertEqual(self.fake.requests[-1]["method"], "action")


class TestResultQuirks(MiioCase):
    device_kwargs = {"values": {(2, 1): 7}, "result_as_string": True}

    def test_json_string_result_is_normalised(self):
        """Some firmwares answer with a JSON *string* instead of an array."""
        device = self.open_device()
        self.addCleanup(device.close)
        values = device.get_properties([(2, 1)])
        self.assertEqual(values, {(2, 1): 7})


class TestRetries(MiioCase):
    device_kwargs = {"drop_first": 2, "values": {(2, 1): 1}}

    def test_power_save_drops_are_retried_on_the_same_socket(self):
        """Wi-Fi power save swallows the first packets; one socket must persist."""
        device = self.open_device(retries=4)
        self.addCleanup(device.close)
        values = device.get_properties([(2, 1)])
        self.assertEqual(values, {(2, 1): 1})
        self.assertEqual(len(self.fake.requests), 1, "only the successful call counts")


class TestStampRepair(MiioCase):
    device_kwargs = {"stamp_error_once": True, "values": {(2, 1): 2}}

    def test_stale_stamp_triggers_rehandshake(self):
        device = self.open_device(retries=2)
        self.addCleanup(device.close)
        values = device.get_properties([(2, 1)])
        self.assertEqual(values, {(2, 1): 2})
        self.assertGreaterEqual(self.fake.handshakes, 2,
                                "client should have re-handshaked")


class TestFailureModes(MiioCase):
    device_kwargs = {"bad_checksum": True}

    def test_bad_checksum_is_rejected(self):
        device = self.open_device(retries=0, timeout=0.4)
        self.addCleanup(device.close)
        self.assertRaises(miio.MiioError, device.info)

    def test_unreachable_device_raises(self):
        # grab a port, then close it so nothing listens
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertRaises(miio.MiioError, miio.MiioDevice,
                          "127.0.0.1", TOKEN, port=port, timeout=0.2, retries=0)


class TestProbe(unittest.TestCase):
    def test_probe_finds_model(self):
        fake = fake_miio.FakeMiioDevice(TOKEN_BYTES, model="minij.washer.v5").start()
        self.addCleanup(fake.stop)
        self.assertEqual(miio.probe("127.0.0.1", TOKEN, port=fake.port),
                         "minij.washer.v5")

    def test_probe_returns_none_when_unreachable(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertIsNone(miio.probe("127.0.0.1", TOKEN, port=port, timeout=0.2))


class TestCryptoContract(unittest.TestCase):
    """Pin the key derivation: key=md5(token), iv=md5(key+token)."""

    def test_key_and_iv(self):
        key, iv = miio.token_key_iv(TOKEN_BYTES)
        self.assertEqual(key, hashlib.md5(TOKEN_BYTES).digest())
        self.assertEqual(iv, hashlib.md5(key + TOKEN_BYTES).digest())

    def test_hex_and_bytes_are_equivalent(self):
        self.assertEqual(miio.token_key_iv(TOKEN), miio.token_key_iv(TOKEN_BYTES))

    def test_bad_token_length(self):
        self.assertRaises(miio.MiioError, miio.token_key_iv, b"short")

    def test_packet_layout(self):
        """Build a packet by hand and check every field offset."""
        device = miio.MiioDevice.__new__(miio.MiioDevice)
        device.key, device.iv = miio.token_key_iv(TOKEN_BYTES)
        device.token = TOKEN_BYTES
        device.did = 0x0A0B0C0D
        device.stamp = 0x01020304
        packet = device._encode(b'{"id":1,"method":"miIO.info","params":[]}')
        magic, length, unknown, did, stamp = struct.unpack(">HHIII", packet[:16])
        self.assertEqual(magic, 0x2131)
        self.assertEqual(length, len(packet))
        self.assertEqual(unknown, 0)
        self.assertEqual(did, 0x0A0B0C0D)
        self.assertEqual(stamp, 0x01020304)
        self.assertEqual(packet[16:32],
                         hashlib.md5(packet[:16] + TOKEN_BYTES + packet[32:]).digest())
        self.assertEqual((len(packet) - 32) % 16, 0, "AES-CBC body must be block aligned")


if __name__ == "__main__":
    unittest.main()
