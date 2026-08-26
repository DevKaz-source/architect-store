from __future__ import annotations

import uuid

from app.payments.base import PixOrderSnapshot, PixProviderError


class MockPixProvider:
    """Provedor local. A aprovação é feita pelo comando approve-mock do CLI."""

    name = "mock"

    async def create_pix(
        self,
        *,
        amount_cents: int,
        external_reference: str,
        payer_email: str,
        idempotency_key: str,
        expiration_minutes: int,
    ) -> PixOrderSnapshot:
        del payer_email, expiration_minutes
        return PixOrderSnapshot(
            provider_order_id=f"MOCK-{uuid.UUID(idempotency_key).hex.upper()}",
            external_reference=external_reference,
            amount_cents=amount_cents,
            status="action_required",
            status_detail="waiting_transfer",
            payment_id=f"MOCKPAY-{uuid.uuid4().hex[:16].upper()}",
            ticket_url=None,
            qr_code=f"PIX-MOCK-{external_reference}",
            qr_code_base64=None,
        )

    async def get_order(self, provider_order_id: str) -> PixOrderSnapshot:
        raise PixProviderError(
            f"{provider_order_id} é um pagamento mock; use o comando approve-mock"
        )
