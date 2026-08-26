from __future__ import annotations

import hashlib
import hmac
import unittest

from cryptography.fernet import Fernet

from app.security import (
    SecretBox,
    mercado_pago_manifest,
    parse_mercado_pago_signature,
    verify_mercado_pago_signature,
)


class SecurityTests(unittest.TestCase):
    def test_secret_box_round_trip(self) -> None:
        box = SecretBox(Fernet.generate_key().decode())
        encrypted = box.encrypt("login@example.com\nsenha-forte")
        self.assertNotIn(b"login@example.com", encrypted)
        self.assertEqual(box.decrypt(encrypted), "login@example.com\nsenha-forte")
        self.assertEqual(box.fingerprint("mesmo"), box.fingerprint("mesmo"))
        self.assertNotEqual(box.fingerprint("mesmo"), box.fingerprint("outro"))

    def test_mercado_pago_signature(self) -> None:
        secret = "webhook-secret"
        data_id = "ORD01ABCDEF"
        request_id = "request-123"
        timestamp = "1742505638683"
        manifest = mercado_pago_manifest(data_id, request_id, timestamp)
        self.assertEqual(
            manifest,
            "id:ord01abcdef;request-id:request-123;ts:1742505638683;",
        )
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        header = f"ts={timestamp},v1={digest}"
        self.assertTrue(
            verify_mercado_pago_signature(
                signature_header=header,
                request_id=request_id,
                data_id=data_id,
                secret=secret,
            )
        )
        self.assertFalse(
            verify_mercado_pago_signature(
                signature_header=header,
                request_id="tampered",
                data_id=data_id,
                secret=secret,
            )
        )

    def test_signature_requires_ts_and_v1(self) -> None:
        with self.assertRaises(ValueError):
            parse_mercado_pago_signature("ts=123")


if __name__ == "__main__":
    unittest.main()
