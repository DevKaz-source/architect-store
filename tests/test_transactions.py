from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db import SessionFactory
from app.models import (
    DeliveryType,
    Deposit,
    DepositStatus,
    Order,
    Product,
    StockItem,
    StockStatus,
    SupportMessage,
    Wallet,
    WalletEntry,
)
from app.payments.base import PixOrderSnapshot
from app.security import SecretBox
from app.services.catalog import purchase_product
from app.services.payments import reconcile_snapshot
from app.services.support import add_user_reply, create_ticket, get_ticket
from app.services.users import accept_terms, get_or_create_user
from app.settings import get_settings


@pytest.mark.asyncio
async def test_purchase_is_atomic_and_idempotent(clean_database) -> None:
    user = await get_or_create_user(
        telegram_id=1001,
        username="cliente",
        first_name="Cliente",
    )
    await accept_terms(user.telegram_id)
    box = SecretBox(get_settings().data_encryption_key)
    async with SessionFactory() as session:
        wallet = await session.get(Wallet, user.id)
        assert wallet is not None
        wallet.balance_cents = 10_000
        product = Product(
            slug="gift-card-test",
            name="Gift Card Teste",
            description="Entrega de teste",
            price_cents=2_500,
            delivery_type=DeliveryType.CODE,
            supplier_reference="TEST-AUTH-001",
            active=True,
        )
        session.add(product)
        await session.flush()
        delivery = "CODIGO-UNICO-123"
        session.add(
            StockItem(
                product_id=product.id,
                payload_ciphertext=box.encrypt(delivery),
                payload_fingerprint=box.fingerprint(delivery),
                status=StockStatus.AVAILABLE,
            )
        )
        await session.commit()
        product_id = product.id

    key = "test:purchase:one"
    first = await purchase_product(
        telegram_id=user.telegram_id,
        product_id=product_id,
        idempotency_key=key,
    )
    second = await purchase_product(
        telegram_id=user.telegram_id,
        product_id=product_id,
        idempotency_key=key,
    )

    assert first.delivery == "CODIGO-UNICO-123"
    assert first.balance_cents == 7_500
    assert second.order_id == first.order_id
    assert second.repeated is True
    async with SessionFactory() as session:
        assert await session.scalar(select(func.count(Order.id))) == 1
        wallet = await session.get(Wallet, user.id)
        assert wallet is not None and wallet.balance_cents == 7_500
        assert await session.scalar(select(func.count(WalletEntry.id))) == 1


@pytest.mark.asyncio
async def test_pix_credit_and_reversal_are_idempotent(clean_database) -> None:
    user = await get_or_create_user(
        telegram_id=1002,
        username=None,
        first_name="Pagador",
    )
    deposit_id = uuid.uuid4()
    external_reference = f"credit_{deposit_id.hex}"
    provider_order_id = "ORDER-TEST-1002"
    async with SessionFactory() as session:
        session.add(
            Deposit(
                id=deposit_id,
                user_id=user.id,
                amount_cents=5_000,
                status=DepositStatus.PENDING,
                provider="mercado_pago",
                provider_order_id=provider_order_id,
                external_reference=external_reference,
            )
        )
        await session.commit()

    approved = PixOrderSnapshot(
        provider_order_id=provider_order_id,
        external_reference=external_reference,
        amount_cents=5_000,
        status="processed",
        status_detail="accredited",
    )
    first = await reconcile_snapshot(approved)
    duplicate = await reconcile_snapshot(approved)
    assert first.changed is True
    assert duplicate.changed is False

    reversed_snapshot = PixOrderSnapshot(
        provider_order_id=provider_order_id,
        external_reference=external_reference,
        amount_cents=5_000,
        status="refunded",
        status_detail="refunded",
    )
    reversal = await reconcile_snapshot(reversed_snapshot)
    repeated_reversal = await reconcile_snapshot(reversed_snapshot)
    assert reversal.changed is True
    assert repeated_reversal.changed is False
    async with SessionFactory() as session:
        wallet = await session.get(Wallet, user.id)
        assert wallet is not None and wallet.balance_cents == 0
        assert await session.scalar(select(func.count(WalletEntry.id))) == 2


@pytest.mark.asyncio
async def test_support_messages_are_encrypted_and_threaded(clean_database) -> None:
    user = await get_or_create_user(
        telegram_id=1003,
        username="suporte",
        first_name="Suporte",
    )
    first_text = "Meu código não ativou"
    ticket = await create_ticket(telegram_id=user.telegram_id, message=first_text)
    await add_user_reply(
        public_code=ticket.public_code,
        telegram_id=user.telegram_id,
        message="Segue mais contexto",
    )
    resolved_ticket, history = await get_ticket(ticket.public_code)
    assert resolved_ticket.public_code == ticket.public_code
    assert history == [
        ("user", first_text),
        ("user", "Segue mais contexto"),
    ]
    async with SessionFactory() as session:
        messages = (await session.scalars(select(SupportMessage))).all()
        assert len(messages) == 2
        assert first_text.encode() not in messages[0].body_ciphertext
