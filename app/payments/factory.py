from __future__ import annotations

from app.payments.base import PixProvider
from app.payments.mercado_pago import MercadoPagoPixProvider
from app.payments.mock import MockPixProvider
from app.settings import Settings


def build_pix_provider(settings: Settings) -> PixProvider:
    if settings.pix_provider == "mercado_pago":
        return MercadoPagoPixProvider(
            settings.mercado_pago_access_token,
            test_mode=settings.mercado_pago_test_mode,
        )
    return MockPixProvider()
