from __future__ import annotations

from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: str = "http://localhost:8000"
    database_url: str = "postgresql+asyncpg://store:change-me@localhost:5432/store"
    redis_url: str | None = None
    log_level: str = "INFO"

    telegram_bot_token: str = "development-token"
    telegram_mode: Literal["webhook", "polling"] = "webhook"
    telegram_webhook_secret: str = "development_webhook_secret"
    admin_telegram_ids: str = ""
    support_chat_id: int | None = None

    pix_provider: Literal["mock", "mercado_pago"] = "mock"
    mercado_pago_access_token: str = ""
    mercado_pago_webhook_secret: str = ""
    mercado_pago_test_mode: bool = False
    pix_expiration_minutes: int = Field(default=30, ge=30, le=43_200)
    min_topup_cents: int = Field(default=500, ge=100)
    max_topup_cents: int = Field(default=200_000, ge=100)
    max_open_deposits_per_user: int = Field(default=3, ge=1, le=20)
    max_open_tickets_per_user: int = Field(default=3, ge=1, le=20)

    giftcard_provider: Literal["none", "mock", "reloadly"] = "none"
    mock_supplier_scenario: Literal[
        "success", "pending_then_success", "ambiguous_then_success", "reject"
    ] = "success"

    # Compatibilidade com instalações anteriores. Prefira GIFTCARD_PROVIDER=reloadly.
    reloadly_enabled: bool = False
    reloadly_environment: Literal["sandbox", "production"] = "sandbox"
    reloadly_client_id: str = ""
    reloadly_client_secret: str = ""
    reloadly_sender_name: str = "Architect Store"
    reloadly_live_enabled: bool = False
    reloadly_timeout_seconds: float = Field(default=20.0, ge=3.0, le=60.0)
    supplier_reconcile_seconds: int = Field(default=60, ge=30, le=3600)
    min_supplier_gross_margin_bps: int = Field(default=500, ge=0, le=5000)

    data_encryption_key: str = ""
    terms_url: str = "https://example.com/termos"
    privacy_url: str = "https://example.com/privacidade"

    @field_validator("app_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("telegram_webhook_secret")
    @classmethod
    def validate_telegram_secret(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if not 1 <= len(value) <= 256 or any(char not in allowed for char in value):
            raise ValueError("TELEGRAM_WEBHOOK_SECRET deve usar apenas A-Z, a-z, 0-9, _ e -")
        return value

    @property
    def admin_ids(self) -> frozenset[int]:
        return frozenset(
            int(item.strip()) for item in self.admin_telegram_ids.split(",") if item.strip()
        )

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.app_base_url}/webhooks/telegram"

    @property
    def active_giftcard_provider(self) -> Literal["none", "mock", "reloadly"]:
        if self.giftcard_provider != "none":
            return self.giftcard_provider
        return "reloadly" if self.reloadly_enabled else "none"

    def validate_runtime(self) -> None:
        errors: list[str] = []
        if self.app_env == "production" and not self.app_base_url.startswith("https://"):
            errors.append("APP_BASE_URL precisa usar HTTPS em produção")
        if self.app_env == "production" and self.telegram_mode != "webhook":
            errors.append("TELEGRAM_MODE precisa ser webhook em produção")
        if self.app_env == "production" and not self.redis_url:
            errors.append("REDIS_URL é obrigatório em produção")
        if self.telegram_bot_token == "development-token":
            errors.append("TELEGRAM_BOT_TOKEN não foi configurado")
        if (
            self.app_env == "production"
            and self.telegram_webhook_secret == "development_webhook_secret"
        ):
            errors.append("TELEGRAM_WEBHOOK_SECRET ainda usa o valor de desenvolvimento")
        if not self.data_encryption_key:
            errors.append("DATA_ENCRYPTION_KEY não foi configurada")
        else:
            try:
                Fernet(self.data_encryption_key.encode())
            except (TypeError, ValueError):
                errors.append("DATA_ENCRYPTION_KEY não é uma chave Fernet válida")
        if not self.admin_ids:
            errors.append("ADMIN_TELEGRAM_IDS não foi configurado")
        if self.pix_provider == "mercado_pago":
            if not self.mercado_pago_access_token:
                errors.append("MERCADO_PAGO_ACCESS_TOKEN não foi configurado")
            if self.telegram_mode == "webhook" and not self.mercado_pago_webhook_secret:
                errors.append("MERCADO_PAGO_WEBHOOK_SECRET não foi configurado")
            if self.app_env == "production" and self.mercado_pago_test_mode:
                errors.append("MERCADO_PAGO_TEST_MODE precisa ser false em produção")
        if self.app_env == "production" and self.pix_provider == "mock":
            errors.append("PIX_PROVIDER=mock não pode ser usado em produção")
        if self.min_topup_cents > self.max_topup_cents:
            errors.append("MIN_TOPUP_CENTS não pode exceder MAX_TOPUP_CENTS")
        if self.active_giftcard_provider == "mock" and self.app_env == "production":
            errors.append("GIFTCARD_PROVIDER=mock não pode ser usado em produção")
        if self.active_giftcard_provider == "reloadly":
            if not self.reloadly_client_id or not self.reloadly_client_secret:
                errors.append("RELOADLY_CLIENT_ID e RELOADLY_CLIENT_SECRET não foram configurados")
            if not self.reloadly_sender_name.strip():
                errors.append("RELOADLY_SENDER_NAME não foi configurado")
            if self.reloadly_environment == "production":
                if self.app_env != "production":
                    errors.append("Reloadly LIVE só pode ser usada com APP_ENV=production")
                if not self.reloadly_live_enabled:
                    errors.append("RELOADLY_LIVE_ENABLED precisa ser true para usar dinheiro real")
        if self.app_env == "production" and any(
            "example.com" in url for url in (self.terms_url, self.privacy_url)
        ):
            errors.append("TERMS_URL e PRIVACY_URL precisam apontar para documentos reais")
        if errors:
            raise RuntimeError("Configuração inválida: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
