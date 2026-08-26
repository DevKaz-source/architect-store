from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.suppliers.base import (
    GiftCardProduct,
    SupplierAmbiguousError,
    SupplierBalance,
    SupplierConfigurationError,
    SupplierError,
    SupplierRedeemCode,
    SupplierRejectedError,
    SupplierTransaction,
    SupplierUnavailableError,
)

AUTH_URL = "https://auth.reloadly.com/oauth/token"
ACCEPT_V1 = "application/com.reloadly.giftcards-v1+json"
ACCEPT_V2 = "application/com.reloadly.giftcards-v2+json"


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _error_fields(response: httpx.Response) -> tuple[str, str | None]:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code} retornado pelo fornecedor", None
    if not isinstance(payload, dict):
        return f"HTTP {response.status_code} retornado pelo fornecedor", None
    message = _text(payload.get("message")) or (
        f"HTTP {response.status_code} retornado pelo fornecedor"
    )
    return message[:400], _text(payload.get("errorCode"))


def _extract_country(payload: dict) -> str:
    country = payload.get("country")
    if isinstance(country, dict):
        return (_text(country.get("isoName")) or _text(country.get("iso")) or "XX").upper()
    return (_text(payload.get("countryCode")) or "XX").upper()


def _parse_product(payload: dict) -> GiftCardProduct:
    product_id = _text(payload.get("productId"))
    name = _text(payload.get("productName"))
    if not product_id or not name:
        raise SupplierError("Produto inválido retornado pela Reloadly")

    fixed_values = tuple(
        value
        for item in payload.get("fixedRecipientDenominations") or []
        if (value := _decimal(item)) is not None
    )
    price_map: dict[Decimal, Decimal] = {}
    for row in payload.get("fixedRecipientToSenderDenominationsMap") or []:
        if not isinstance(row, dict):
            continue
        for recipient, sender in row.items():
            recipient_value = _decimal(recipient)
            sender_value = _decimal(sender)
            if recipient_value is not None and sender_value is not None:
                price_map[recipient_value] = sender_value

    requirements = payload.get("additionalRequirements")
    user_id_required = bool(
        isinstance(requirements, dict) and requirements.get("userIdRequired") is True
    )
    return GiftCardProduct(
        product_id=product_id,
        name=name,
        country_code=_extract_country(payload),
        status=(_text(payload.get("status")) or "ACTIVE").upper(),
        denomination_type=(_text(payload.get("denominationType")) or "UNKNOWN").upper(),
        recipient_currency=(_text(payload.get("recipientCurrencyCode")) or "XXX").upper(),
        sender_currency=(_text(payload.get("senderCurrencyCode")) or "XXX").upper(),
        fixed_recipient_denominations=fixed_values,
        recipient_to_sender=price_map,
        min_recipient_denomination=_decimal(payload.get("minRecipientDenomination")),
        max_recipient_denomination=_decimal(
            payload.get("maxRecipientDenomination", payload.get("maxrecipientDenomination"))
        ),
        discount_percentage=_decimal(payload.get("discountPercentage")),
        user_id_required=user_id_required,
        raw=payload,
    )


def _parse_transaction(payload: dict) -> SupplierTransaction:
    transaction_id = _text(payload.get("transactionId"))
    custom_identifier = _text(payload.get("customIdentifier"))
    if not transaction_id or not custom_identifier:
        raise SupplierError("Transação inválida retornada pela Reloadly")
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    quantity = product.get("quantity")
    return SupplierTransaction(
        transaction_id=transaction_id,
        custom_identifier=custom_identifier,
        status=(_text(payload.get("status")) or "UNKNOWN").upper(),
        amount=_decimal(payload.get("amount")),
        currency=(_text(payload.get("currencyCode")) or "").upper() or None,
        product_id=_text(product.get("productId")),
        unit_price=_decimal(product.get("unitPrice")),
        quantity=(
            int(quantity)
            if isinstance(quantity, int) and not isinstance(quantity, bool)
            else None
        ),
    )


def _transaction_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            return [row for row in content if isinstance(row, dict)]
        if payload.get("transactionId") is not None:
            return [payload]
    return []


class ReloadlyClient:
    name = "reloadly"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        environment: str,
        timeout_seconds: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if environment not in {"sandbox", "production"}:
            raise ValueError("Ambiente Reloadly inválido")
        self.environment = environment
        self.client_id = client_id
        self.client_secret = client_secret
        suffix = "-sandbox" if environment == "sandbox" else ""
        self.base_url = f"https://giftcards{suffix}.reloadly.com"
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._owns_client = http_client is None
        self._access_token: str | None = None
        self._token_valid_until = 0.0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _token(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if not force_refresh and self._access_token and now < self._token_valid_until:
            return self._access_token
        try:
            response = await self._client.post(
                AUTH_URL,
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                    "audience": self.base_url,
                },
            )
        except httpx.RequestError as exc:
            raise SupplierUnavailableError("Não foi possível autenticar na Reloadly") from exc
        if response.status_code != 200:
            message, code = _error_fields(response)
            raise SupplierConfigurationError(message, status_code=response.status_code, code=code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise SupplierConfigurationError(
                "Resposta de autenticação inválida da Reloadly"
            ) from exc
        token = _text(payload.get("access_token")) if isinstance(payload, dict) else None
        expires_in = payload.get("expires_in", 300) if isinstance(payload, dict) else 300
        if not token or not isinstance(expires_in, (int, float)):
            raise SupplierConfigurationError("Token inválido retornado pela Reloadly")
        self._access_token = token
        self._token_valid_until = now + max(30.0, float(expires_in) - 60.0)
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        accept: str = ACCEPT_V1,
        order_request: bool = False,
    ) -> Any:
        response: httpx.Response | None = None
        for attempt in range(2):
            token = await self._token(force_refresh=attempt == 1)
            try:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": accept,
                        "Content-Type": "application/json",
                    },
                )
            except httpx.RequestError as exc:
                error_type = SupplierAmbiguousError if order_request else SupplierUnavailableError
                raise error_type(
                    "A Reloadly não respondeu; a operação precisa ser conciliada"
                ) from exc
            if response.status_code != 401 or attempt == 1:
                break
            self._access_token = None

        assert response is not None
        if not 200 <= response.status_code < 300:
            message, code = _error_fields(response)
            if order_request:
                duplicate_hint = "identifier" in message.lower() and any(
                    word in message.lower() for word in ("unique", "duplicate", "already")
                )
                ambiguous_status = (
                    response.status_code in {408, 409, 425, 429}
                    or response.status_code >= 500
                )
                if duplicate_hint or ambiguous_status:
                    raise SupplierAmbiguousError(
                        message, status_code=response.status_code, code=code
                    )
                raise SupplierRejectedError(message, status_code=response.status_code, code=code)
            raise SupplierUnavailableError(message, status_code=response.status_code, code=code)
        try:
            return response.json()
        except ValueError as exc:
            error_type = SupplierAmbiguousError if order_request else SupplierUnavailableError
            raise error_type("Resposta inválida retornada pela Reloadly") from exc

    async def get_balance(self) -> SupplierBalance:
        payload = await self._request("GET", "/accounts/balance")
        if not isinstance(payload, dict):
            raise SupplierError("Saldo inválido retornado pela Reloadly")
        amount = _decimal(payload.get("balance"))
        currency = (_text(payload.get("currencyCode")) or "").upper()
        if amount is None or not currency:
            raise SupplierError("Saldo inválido retornado pela Reloadly")
        return SupplierBalance(amount=amount, currency=currency)

    async def list_products(self, country_code: str) -> list[GiftCardProduct]:
        code = country_code.strip().upper()
        if len(code) != 2 or not code.isalpha():
            raise ValueError("Código de país precisa ter duas letras, como BR")
        payload = await self._request("GET", f"/countries/{code}/products")
        if not isinstance(payload, list):
            raise SupplierError("Catálogo inválido retornado pela Reloadly")
        products = [_parse_product(row) for row in payload if isinstance(row, dict)]
        return [
            replace(product, country_code=code) if product.country_code == "XX" else product
            for product in products
        ]

    async def get_product(self, product_id: str) -> GiftCardProduct:
        normalized = product_id.strip()
        if not normalized.isdigit():
            raise ValueError("O product ID da Reloadly precisa ser numérico")
        payload = await self._request("GET", f"/products/{normalized}")
        if not isinstance(payload, dict):
            raise SupplierError("Produto inválido retornado pela Reloadly")
        return _parse_product(payload)

    async def order(
        self,
        *,
        product_id: str,
        unit_price: Decimal,
        custom_identifier: str,
        sender_name: str,
    ) -> SupplierTransaction:
        payload = await self._request(
            "POST",
            "/orders",
            json={
                "productId": int(product_id),
                "quantity": 1,
                "unitPrice": float(unit_price),
                "customIdentifier": custom_identifier,
                "senderName": sender_name,
            },
            order_request=True,
        )
        if not isinstance(payload, dict):
            raise SupplierAmbiguousError(
                "Pedido aceito com resposta inválida; conciliação necessária"
            )
        try:
            return _parse_transaction(payload)
        except SupplierError as exc:
            raise SupplierAmbiguousError(str(exc)) from exc

    async def get_transaction(self, transaction_id: str) -> SupplierTransaction:
        payload = await self._request("GET", f"/reports/transactions/{transaction_id}")
        rows = _transaction_rows(payload)
        matches = [row for row in rows if _text(row.get("transactionId")) == transaction_id]
        if len(matches) != 1:
            raise SupplierUnavailableError("A Reloadly não retornou uma transação única")
        return _parse_transaction(matches[0])

    async def find_transaction(self, custom_identifier: str) -> SupplierTransaction | None:
        payload = await self._request(
            "GET",
            "/reports/transactions",
            params={"customIdentifier": custom_identifier, "size": 10},
        )
        rows = _transaction_rows(payload)
        matches = [
            row for row in rows if _text(row.get("customIdentifier")) == custom_identifier
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise SupplierAmbiguousError("Mais de uma transação usa o mesmo identificador")
        return _parse_transaction(matches[0])

    async def get_redeem_code(self, transaction_id: str) -> SupplierRedeemCode:
        payload = await self._request(
            "GET",
            f"/orders/transactions/{transaction_id}/cards",
            accept=ACCEPT_V2,
        )
        if isinstance(payload, list):
            payload = payload[0] if len(payload) == 1 else None
        if not isinstance(payload, dict):
            raise SupplierUnavailableError("Código inválido retornado pela Reloadly")
        code = SupplierRedeemCode(
            card_number=_text(payload.get("cardNumber")),
            pin_code=_text(payload.get("pinCode")),
            redemption_url=_text(payload.get("redemptionUrl")),
        )
        code.as_delivery()
        return code
