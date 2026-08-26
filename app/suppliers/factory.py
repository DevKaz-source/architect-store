from __future__ import annotations

from app.settings import Settings
from app.suppliers.base import GiftCardSupplier
from app.suppliers.mock import MockGiftCardSupplier
from app.suppliers.reloadly import ReloadlyClient


def configured_giftcard_supplier_identity(settings: Settings) -> tuple[str, str] | None:
    provider = settings.active_giftcard_provider
    if provider == "none":
        return None
    if provider == "mock":
        return "mock", "sandbox"
    return "reloadly", settings.reloadly_environment


def build_giftcard_supplier(settings: Settings) -> GiftCardSupplier | None:
    provider = settings.active_giftcard_provider
    if provider == "none":
        return None
    if provider == "mock":
        if settings.app_env == "production":
            raise ValueError("O fornecedor mock não pode ser usado em produção")
        return MockGiftCardSupplier(scenario=settings.mock_supplier_scenario)
    if not settings.reloadly_client_id or not settings.reloadly_client_secret:
        raise ValueError("Credenciais Reloadly não foram configuradas")
    if settings.reloadly_environment == "production" and (
        settings.app_env != "production" or not settings.reloadly_live_enabled
    ):
        raise ValueError(
            "Reloadly LIVE exige APP_ENV=production e RELOADLY_LIVE_ENABLED=true"
        )
    return ReloadlyClient(
        client_id=settings.reloadly_client_id,
        client_secret=settings.reloadly_client_secret,
        environment=settings.reloadly_environment,
        timeout_seconds=settings.reloadly_timeout_seconds,
    )
