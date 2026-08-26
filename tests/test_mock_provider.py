from __future__ import annotations

import asyncio
import unittest
import uuid

from app.payments.mock import MockPixProvider


class MockProviderTests(unittest.TestCase):
    def test_create_pix_is_pending_and_preserves_value(self) -> None:
        provider = MockPixProvider()
        key = str(uuid.uuid4())
        snapshot = asyncio.run(
            provider.create_pix(
                amount_cents=2590,
                external_reference="credit_123",
                payer_email="buyer@example.com",
                idempotency_key=key,
                expiration_minutes=30,
            )
        )
        self.assertEqual(snapshot.amount_cents, 2590)
        self.assertEqual(snapshot.external_reference, "credit_123")
        self.assertEqual(snapshot.status, "action_required")
        self.assertEqual(snapshot.status_detail, "waiting_transfer")


if __name__ == "__main__":
    unittest.main()
