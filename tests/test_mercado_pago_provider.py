from __future__ import annotations

import pytest

from app.payments.mercado_pago import MercadoPagoPixProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_mode", "expected_email", "expected_name"),
    [
        (True, "test_user_br@testuser.com", "APRO"),
        (False, "buyer@example.com", None),
    ],
)
async def test_create_pix_builds_expected_payer(
    monkeypatch: pytest.MonkeyPatch,
    test_mode: bool,
    expected_email: str,
    expected_name: str | None,
) -> None:
    provider = MercadoPagoPixProvider("test-token", test_mode=test_mode)
    captured: dict = {}

    async def fake_request(method, path, *, json=None, idempotency_key=None):
        captured.update(
            method=method,
            path=path,
            json=json,
            idempotency_key=idempotency_key,
        )
        return {
            "id": "ORD-TEST-1",
            "external_reference": "credit_test",
            "total_amount": "50.00",
            "status": "action_required",
            "status_detail": "waiting_transfer",
            "transactions": {
                "payments": [
                    {
                        "id": "PAY-TEST-1",
                        "payment_method": {
                            "id": "pix",
                            "type": "bank_transfer",
                            "qr_code": "000201-test",
                        },
                    }
                ]
            },
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    snapshot = await provider.create_pix(
        amount_cents=5000,
        external_reference="credit_test",
        payer_email="buyer@example.com",
        idempotency_key="deposit-test-1",
        expiration_minutes=30,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/orders"
    assert captured["idempotency_key"] == "deposit-test-1"
    assert captured["json"]["payer"]["email"] == expected_email
    assert captured["json"]["payer"].get("first_name") == expected_name
    assert snapshot.provider_order_id == "ORD-TEST-1"
