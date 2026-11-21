"""Crypto test vectors (FIPS-197, SP 800-38A/D, RC4 known answers).

Run: python -m unittest discover -s tests -v
"""

import binascii
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.crypto import aes, rc4  # noqa: E402


def h(s):
    return binascii.unhexlify(s.replace(" ", ""))


class TestAESBlock(unittest.TestCase):
    def test_128(self):
        c = aes.AES(h("000102030405060708090a0b0c0d0e0f"))
        ct = c.encrypt_block(h("00112233445566778899aabbccddeeff"))
        self.assertEqual(binascii.hexlify(ct).decode(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(c.decrypt_block(ct), h("00112233445566778899aabbccddeeff"))

    def test_192(self):
        c = aes.AES(h("000102030405060708090a0b0c0d0e0f1011121314151617"))
        ct = c.encrypt_block(h("00112233445566778899aabbccddeeff"))
        self.assertEqual(binascii.hexlify(ct).decode(), "dda97ca4864cdfe06eaf70a0ec0d7191")
        self.assertEqual(c.decrypt_block(ct), h("00112233445566778899aabbccddeeff"))

    def test_256(self):
        c = aes.AES(h("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"))
        ct = c.encrypt_block(h("00112233445566778899aabbccddeeff"))
        self.assertEqual(binascii.hexlify(ct).decode(), "8ea2b7ca516745bfeafc49904b496089")
        self.assertEqual(c.decrypt_block(ct), h("00112233445566778899aabbccddeeff"))

    def test_reject_bad_key(self):
        self.assertRaises(ValueError, aes.AES, b"short")


class TestCBCCase(unittest.TestCase):
    """SP 800-38A F.2.1 (AES-128-CBC)."""

    KEY = h("2b7e151628aed2a6abf7158809cf4f3c")
    IV = h("000102030405060708090a0b0c0d0e0f")
    PT = h("6bc1bee22e409f96e93d7e117393172a"
           "ae2d8a571e03ac9c9eb76fac45af8e51")
    CT = h("7649abac8119b246cee98e9b12e9197d"
           "5086cb9b507219ee95db113a917678b2")

    def test_encrypt_prefix(self):
        out = aes.cbc_encrypt(self.KEY, self.IV, self.PT)
        self.assertEqual(out[:32], self.CT)
        self.assertEqual(len(out), 48)  # + one PKCS#7 block

    def test_roundtrip(self):
        out = aes.cbc_encrypt(self.KEY, self.IV, self.PT)
        self.assertEqual(aes.cbc_decrypt(self.KEY, self.IV, out), self.PT)

    def test_roundtrip_various_lengths(self):
        for n in (0, 1, 15, 16, 17, 33):
            data = bytes(bytearray(range(n)))
            out = aes.cbc_encrypt(self.KEY, self.IV, data)
            self.assertEqual(aes.cbc_decrypt(self.KEY, self.IV, out), data)


class TestGCM(unittest.TestCase):
    """GCM spec, test case 14 (AES-256, zero key/iv)."""

    KEY = b"\x00" * 32
    IV = b"\x00" * 12

    def test_encrypt_empty(self):
        ct, tag = aes.gcm_encrypt(self.KEY, self.IV, b"")
        self.assertEqual(binascii.hexlify(ct).decode(), "")
        self.assertEqual(binascii.hexlify(tag).decode(),
                         "530f8afbc74536b9a963b4f1c4cb738b")

    def test_encrypt_one_block(self):
        ct, tag = aes.gcm_encrypt(self.KEY, self.IV, b"\x00" * 16)
        self.assertEqual(binascii.hexlify(ct).decode(),
                         "cea7403d4d606b6e074ec5d3baf39d18")
        self.assertEqual(binascii.hexlify(tag).decode(),
                         "d0d1c8a799996bf0265b98b5d48ab919")

    def test_roundtrip_with_aad(self):
        pt = b'{"params":[{"did":"123","siid":2,"piid":1}]}'
        ct, tag = aes.gcm_encrypt(self.KEY, self.IV, pt, aad=b"head")
        self.assertEqual(aes.gcm_decrypt(self.KEY, self.IV, ct, tag, aad=b"head"), pt)

    def test_tag_mismatch(self):
        ct, tag = aes.gcm_encrypt(self.KEY, self.IV, b"abc")
        bad = bytes(bytearray([tag[0] ^ 1])) + tag[1:]
        self.assertRaises(ValueError, aes.gcm_decrypt, self.KEY, self.IV, ct, bad)


class TestRC4(unittest.TestCase):
    def test_known_answer(self):
        # Classic RC4 vector: "Key" / "Plaintext" -> BBF316E8D940AF0AD3
        out = rc4.RC4(b"Key").crypt(b"Plaintext")
        self.assertEqual(binascii.hexlify(out).decode(), "bbf316e8d940af0ad3")

    def test_burn_advances_same_state(self):
        """Burning 1024 bytes must change the keystream of later data."""
        key = b"\x01" * 32
        plain = rc4.RC4(key)
        plain.skip(rc4.BURN_BYTES)
        first = plain.crypt(b"hello")

        burned = rc4.new_stream(key)
        self.assertEqual(burned.crypt(b"hello"), first)

        # A stream that did NOT burn must differ.
        fresh = rc4.RC4(key)
        self.assertNotEqual(fresh.crypt(b"hello"), first)

    def test_state_is_resumable(self):
        key = b"\x02" * 32
        a = rc4.new_stream(key)
        chunked = a.crypt(b"ab") + a.crypt(b"cd")
        b = rc4.new_stream(key)
        self.assertEqual(b.crypt(b"abcd"), chunked)

    def test_encrypt_decrypt_b64(self):
        key = b"\x03" * 32
        blob = rc4.encrypt_b64(key, '{"x":"中文"}')
        self.assertEqual(rc4.decrypt_b64(key, blob).decode("utf-8"), '{"x":"中文"}')


if __name__ == "__main__":
    unittest.main()
