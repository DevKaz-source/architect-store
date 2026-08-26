from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.suppliers.base import SupplierAmbiguousError, minimum_sale_for_gross_margin
from app.suppliers.reloadly import ReloadlyClient


def _json_response(
    request: httpx.Request, payload: object, status_code: int = 200
) -> httpx.Response:
    return httpx.Response(status_code, request=request, json=payload)


def test_minimum_sale_uses_true_gross_margin() -> None:
    assert minimum_sale_for_gross_margin(Decimal("95"), 500) == Decimal("100")


@pytest.mark.asyncio
async def test_reloadly_catalog_order_and_redeem_code() -> None:
    seen_order: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://auth.reloadly.com/oauth/token"):
            return _json_response(
                request,
                {"access_token": "safe-token", "expires_in": 3600, "token_type": "Bearer"},
            )
        assert request.headers["authorization"] == "Bearer safe-token"
        if request.url.path == "/countries/BR/products":
            return _json_response(
                request,
                [
                    {
                        "productId": 3058,
                        "productName": "Gift Card BR",
                        "status": "ACTIVE",
                        "denominationType": "FIXED",
                        "recipientCurrencyCode": "BRL",
                        "senderCurrencyCode": "BRL",
                        "fixedRecipientDenominations": [50],
                        "fixedRecipientToSenderDenominationsMap": [{"50.00": 48.5}],
                        "discountPercentage": 3,
                        "additionalRequirements": {"userIdRequired": False},
                    }
                ],
            )
        if request.url.path == "/orders":
            seen_order.update(json.loads(request.content))
            return _json_response(
                request,
                {
                    "transactionId": 9001,
                    "customIdentifier": "AST-123",
                    "status": "SUCCESSFUL",
                    "amount": 48.5,
                    "currencyCode": "BRL",
                    "product": {
                        "productId": 3058,
                        "unitPrice": 50,
                        "quantity": 1,
                    },
                },
            )
        if request.url.path == "/orders/transactions/9001/cards":
            assert request.headers["accept"].endswith("giftcards-v2+json")
            return _json_response(
                request,
                {"cardNumber": "CODE-123", "pinCode": "7788", "redemptionUrl": None},
            )
        raise AssertionError(f"Request inesperada: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    supplier = ReloadlyClient(
        client_id="client",
        client_secret="secret",
        environment="sandbox",
        http_client=http_client,
    )
    products = await supplier.list_products("BR")
    assert products[0].recipient_to_sender[Decimal("50")] == Decimal("48.5")
    assert products[0].estimated_sender_cost(Decimal("50")) == Decimal("48.5")

    transaction = await supplier.order(
        product_id="3058",
        unit_price=Decimal("50"),
        custom_identifier="AST-123",
        sender_name="Architect Store",
    )
    code = await supplier.get_redeem_code(transaction.transaction_id)

    assert transaction.status == "SUCCESSFUL"
    assert code.as_delivery() == "Código: CODE-123\nPIN: 7788"
    assert seen_order == {
        "productId": 3058,
        "quantity": 1,
        "unitPrice": 50.0,
        "customIdentifier": "AST-123",
        "senderName": "Architect Store",
    }
    await http_client.aclose()


@pytest.mark.asyncio
async def test_reloadly_timeout_on_order_is_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.reloadly.com":
            return _json_response(request, {"access_token": "token", "expires_in": 3600})
        raise httpx.ConnectTimeout("timeout", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    supplier = ReloadlyClient(
        client_id="client",
        client_secret="secret",
        environment="sandbox",
        http_client=http_client,
    )
    with pytest.raises(SupplierAmbiguousError):
        await supplier.order(
            product_id="1",
            unit_price=Decimal("10"),
            custom_identifier="AST-timeout",
            sender_name="Architect Store",
        )
    await http_client.aclose()
