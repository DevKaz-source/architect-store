from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.money import cents_to_api_amount
from app.payments.base import PixOrderSnapshot, PixProviderError, PixProviderRejected


def _api_amount_to_cents(raw: Any) -> int:
    try:
        return int((Decimal(str(raw)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PixProviderError("O provedor retornou um valor inválido") from exc


def parse_order(payload: dict[str, Any]) -> PixOrderSnapshot:
    payments = payload.get("transactions", {}).get("payments", [])
    payment = payments[0] if payments else {}
    method = payment.get("payment_method", {})
    provider_order_id = str(payload.get("id") or "")
    external_reference = str(payload.get("external_reference") or "")
    if not provider_order_id or not external_reference:
        raise PixProviderError("Resposta do Pix sem identificadores obrigatórios")

    return PixOrderSnapshot(
        provider_order_id=provider_order_id,
        external_reference=external_reference,
        amount_cents=_api_amount_to_cents(payload.get("total_amount")),
        status=str(payload.get("status") or payment.get("status") or "unknown"),
        status_detail=str(
            payload.get("status_detail") or payment.get("status_detail") or "unknown"
        ),
        payment_id=str(payment["id"]) if payment.get("id") else None,
        ticket_url=method.get("ticket_url"),
        qr_code=method.get("qr_code"),
        qr_code_base64=method.get("qr_code_base64"),
    )


class MercadoPagoPixProvider:
    name = "mercado_pago"
    api_base_url = "https://api.mercadopago.com"

    def __init__(self, access_token: str, *, test_mode: bool = False) -> None:
        if not access_token:
            raise ValueError("Access Token do Mercado Pago ausente")
        self._access_token = access_token
        self._test_mode = test_mode

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = self._auth_headers
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(base_url=self.api_base_url, timeout=timeout) as client:
            for attempt in range(2):
                try:
                    response = await client.request(method, path, json=json, headers=headers)
                except httpx.TransportError as exc:
                    if attempt == 0 and idempotency_key:
                        await asyncio.sleep(0.25)
                        continue
                    raise PixProviderError("Falha de comunicação com o provedor Pix") from exc

                if response.status_code == 429:
                    raise PixProviderError("Limite temporário do provedor Pix; tente novamente")
                if response.status_code >= 500:
                    if attempt == 0 and idempotency_key:
                        await asyncio.sleep(0.25)
                        continue
                    raise PixProviderError(f"Provedor Pix indisponível ({response.status_code})")
                if response.is_error:
                    try:
                        detail = response.json()
                    except ValueError:
                        detail = response.text[:300]
                    raise PixProviderRejected(
                        f"Provedor recusou a operação ({response.status_code}): {detail}"
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise PixProviderError("Resposta inválida do provedor Pix") from exc

        raise PixProviderError("Não foi possível concluir a operação Pix")

    async def create_pix(
        self,
        *,
        amount_cents: int,
        external_reference: str,
        payer_email: str,
        idempotency_key: str,
        expiration_minutes: int,
    ) -> PixOrderSnapshot:
        amount = cents_to_api_amount(amount_cents)
        payer = {"email": payer_email}
        if self._test_mode:
            # Cenário oficial da API de Orders: APRO faz a ordem de teste
            # avançar automaticamente de waiting_transfer para aprovada. O sandbox
            # também exige um endereço do domínio testuser.com.
            payer["email"] = "test_user_br@testuser.com"
            payer["first_name"] = "APRO"

        payload = {
            "type": "online",
            "total_amount": amount,
            "external_reference": external_reference,
            "processing_mode": "automatic",
            "transactions": {
                "payments": [
                    {
                        "amount": amount,
                        "payment_method": {"id": "pix", "type": "bank_transfer"},
                        "expiration_time": f"PT{expiration_minutes}M",
                    }
                ]
            },
            "payer": payer,
        }
        response = await self._request(
            "POST", "/v1/orders", json=payload, idempotency_key=idempotency_key
        )
        return parse_order(response)

    async def get_order(self, provider_order_id: str) -> PixOrderSnapshot:
        response = await self._request("GET", f"/v1/orders/{provider_order_id}")
        return parse_order(response)
