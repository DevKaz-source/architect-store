from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PixProviderError(RuntimeError):
    pass


class PixProviderRejected(PixProviderError):
    pass


@dataclass(frozen=True)
class PixOrderSnapshot:
    provider_order_id: str
    external_reference: str
    amount_cents: int
    status: str
    status_detail: str
    payment_id: str | None = None
    ticket_url: str | None = None
    qr_code: str | None = None
    qr_code_base64: str | None = None


class PixProvider(Protocol):
    name: str

    async def create_pix(
        self,
        *,
        amount_cents: int,
        external_reference: str,
        payer_email: str,
        idempotency_key: str,
        expiration_minutes: int,
    ) -> PixOrderSnapshot: ...

    async def get_order(self, provider_order_id: str) -> PixOrderSnapshot: ...
