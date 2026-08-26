from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db import SessionFactory
from app.models import (
    DeliveryType,
    FulfillmentMode,
    Order,
    OrderStatus,
    Product,
    SupplierOffer,
    SupplierOrder,
    Wallet,
    WalletEntry,
)
from app.services.catalog import purchase_product, reconcile_supplier_order
from app.services.users import accept_terms, get_or_create_user
from app.suppliers.base import (
    GiftCardProduct,
    SupplierAmbiguousError,
    SupplierBalance,
    SupplierRedeemCode,
    SupplierTransaction,
)


class FakeSupplier:
    name = "reloadly"
    environment = "sandbox"

    def __init__(self, mode: str = "success", live_cost: Decimal = Decimal("8.50")) -> None:
        self.mode = mode
        self.live_cost = live_cost
        self.order_calls = 0

    async def get_balance(self) -> SupplierBalance:
        return SupplierBalance(amount=Decimal("1000"), currency="BRL")

    async def list_products(self, country_code: str) -> list[GiftCardProduct]:
        return []

    async def get_product(self, product_id: str) -> GiftCardProduct:
        return GiftCardProduct(
            product_id="777",
            name="Gift Card Reloadly",
            country_code="BR",
            status="ACTIVE",
            denomination_type="FIXED",
            recipient_currency="BRL",
            sender_currency="BRL",
            fixed_recipient_denominations=(Decimal("10"),),
            recipient_to_sender={Decimal("10"): self.live_cost},
            min_recipient_denomination=None,
            max_recipient_denomination=None,
            discount_percentage=Decimal("15"),
            user_id_required=False,
            raw={},
        )

    def transaction(self, custom_identifier: str) -> SupplierTransaction:
        return SupplierTransaction(
            transaction_id="TX-9001",
            custom_identifier=custom_identifier,
            status="SUCCESSFUL",
            amount=Decimal("8.50"),
            currency="BRL",
            product_id="777",
            unit_price=Decimal("10"),
            quantity=1,
        )

    async def order(
        self,
        *,
        product_id: str,
        unit_price: Decimal,
        custom_identifier: str,
        sender_name: str,
    ) -> SupplierTransaction:
        self.order_calls += 1
        if self.mode == "ambiguous":
            raise SupplierAmbiguousError("timeout")
        return self.transaction(custom_identifier)

    async def get_transaction(self, transaction_id: str) -> SupplierTransaction:
        async with SessionFactory() as session:
            supplier_order = await session.scalar(
                select(SupplierOrder).where(
                    SupplierOrder.external_transaction_id == transaction_id
                )
            )
            assert supplier_order is not None
            return self.transaction(supplier_order.custom_identifier)

    async def find_transaction(self, custom_identifier: str) -> SupplierTransaction | None:
        return self.transaction(custom_identifier)

    async def get_redeem_code(self, transaction_id: str) -> SupplierRedeemCode:
        return SupplierRedeemCode(
            card_number="RELOADLY-CODE-123",
            pin_code="7788",
            redemption_url=None,
        )

    async def aclose(self) -> None:
        return None


async def _create_supplier_product(telegram_id: int) -> tuple[int, int]:
    user = await get_or_create_user(
        telegram_id=telegram_id,
        username="supplier-client",
        first_name="Cliente",
    )
    await accept_terms(telegram_id)
    async with SessionFactory() as session:
        wallet = await session.get(Wallet, user.id)
        assert wallet is not None
        wallet.balance_cents = 5_000
        product = Product(
            slug=f"reloadly-{telegram_id}",
            name="Gift Card Reloadly",
            description="Entrega automática",
            price_cents=1_000,
            delivery_type=DeliveryType.CODE,
            fulfillment_mode=FulfillmentMode.SUPPLIER,
            supplier_reference="reloadly:sandbox:777:10",
            active=True,
        )
        session.add(product)
        await session.flush()
        session.add(
            SupplierOffer(
                product_id=product.id,
                supplier="reloadly",
                environment="sandbox",
                external_product_id="777",
                country_code="BR",
                unit_price=Decimal("10"),
                recipient_currency="BRL",
                sender_currency="BRL",
                estimated_sender_cost=Decimal("8.50"),
                discount_percentage=Decimal("15"),
                active=True,
            )
        )
        await session.commit()
        return user.id, product.id


@pytest.mark.asyncio
async def test_supplier_purchase_delivers_once(clean_database) -> None:
    user_id, product_id = await _create_supplier_product(2001)
    supplier = FakeSupplier()
    first = await purchase_product(
        telegram_id=2001,
        product_id=product_id,
        idempotency_key="supplier:success:1",
        supplier=supplier,
    )
    second = await purchase_product(
        telegram_id=2001,
        product_id=product_id,
        idempotency_key="supplier:success:1",
        supplier=supplier,
    )

    assert first.status == OrderStatus.DELIVERED
    assert first.delivery == "Código: RELOADLY-CODE-123\nPIN: 7788"
    assert first.balance_cents == 4_000
    assert second.order_id == first.order_id
    assert second.repeated is True
    assert supplier.order_calls == 1
    async with SessionFactory() as session:
        wallet = await session.get(Wallet, user_id)
        assert wallet is not None and wallet.balance_cents == 4_000
        assert await session.scalar(select(func.count(Order.id))) == 1
        assert await session.scalar(select(func.count(WalletEntry.id))) == 1


@pytest.mark.asyncio
async def test_ambiguous_supplier_order_is_reconciled_without_second_purchase(
    clean_database,
) -> None:
    _user_id, product_id = await _create_supplier_product(2002)
    supplier = FakeSupplier(mode="ambiguous")
    pending = await purchase_product(
        telegram_id=2002,
        product_id=product_id,
        idempotency_key="supplier:ambiguous:1",
        supplier=supplier,
    )
    repeated = await purchase_product(
        telegram_id=2002,
        product_id=product_id,
        idempotency_key="supplier:ambiguous:1",
        supplier=supplier,
    )

    assert pending.status == OrderStatus.REVIEW
    assert pending.delivery is None
    assert repeated.order_id == pending.order_id
    assert supplier.order_calls == 1

    completed = await reconcile_supplier_order(pending.public_code, supplier)
    assert completed.status == OrderStatus.DELIVERED
    assert completed.delivery == "Código: RELOADLY-CODE-123\nPIN: 7788"
    assert supplier.order_calls == 1


@pytest.mark.asyncio
async def test_supplier_cost_guard_refunds_and_pauses_product(clean_database) -> None:
    user_id, product_id = await _create_supplier_product(2003)
    supplier = FakeSupplier(live_cost=Decimal("12"))
    result = await purchase_product(
        telegram_id=2003,
        product_id=product_id,
        idempotency_key="supplier:margin:1",
        supplier=supplier,
    )

    assert result.status == OrderStatus.REFUNDED
    assert result.balance_cents == 5_000
    assert supplier.order_calls == 0
    async with SessionFactory() as session:
        wallet = await session.get(Wallet, user_id)
        product = await session.get(Product, product_id)
        assert wallet is not None and wallet.balance_cents == 5_000
        assert product is not None and product.active is False
        assert await session.scalar(select(func.count(WalletEntry.id))) == 2
