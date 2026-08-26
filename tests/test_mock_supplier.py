from __future__ import annotations

import argparse
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.cli import seed_mock_products
from app.db import SessionFactory
from app.models import Product, SupplierOffer
from app.services.catalog import list_catalog
from app.settings import Settings
from app.suppliers.base import SupplierAmbiguousError, SupplierRejectedError
from app.suppliers.factory import (
    build_giftcard_supplier,
    configured_giftcard_supplier_identity,
)
from app.suppliers.mock import MockGiftCardSupplier


@pytest.mark.asyncio
async def test_mock_supplier_delivers_a_clearly_fake_code() -> None:
    supplier = MockGiftCardSupplier()
    transaction = await supplier.order(
        product_id="900001",
        unit_price=Decimal("10"),
        custom_identifier="AST-TEST-1",
        sender_name="Architect Store",
    )
    repeated = await supplier.order(
        product_id="900001",
        unit_price=Decimal("10"),
        custom_identifier="AST-TEST-1",
        sender_name="Architect Store",
    )
    code = await supplier.get_redeem_code(transaction.transaction_id)

    assert transaction.status == "SUCCESSFUL"
    assert repeated == transaction
    assert code.card_number is not None
    assert code.card_number.startswith("TESTE-SEM-VALOR-")
    assert code.pin_code == "0000"


@pytest.mark.asyncio
async def test_pending_mock_transaction_succeeds_on_reconciliation() -> None:
    supplier = MockGiftCardSupplier(scenario="pending_then_success")
    pending = await supplier.order(
        product_id="900002",
        unit_price=Decimal("20"),
        custom_identifier="AST-TEST-2",
        sender_name="Architect Store",
    )
    restarted_supplier = MockGiftCardSupplier(scenario="pending_then_success")
    completed = await restarted_supplier.get_transaction(pending.transaction_id)

    assert pending.status == "PENDING"
    assert completed.status == "SUCCESSFUL"
    assert completed.custom_identifier == pending.custom_identifier


@pytest.mark.asyncio
async def test_ambiguous_and_rejected_mock_scenarios() -> None:
    ambiguous = MockGiftCardSupplier(scenario="ambiguous_then_success")
    with pytest.raises(SupplierAmbiguousError):
        await ambiguous.order(
            product_id="900003",
            unit_price=Decimal("35"),
            custom_identifier="AST-TEST-3",
            sender_name="Architect Store",
        )
    found = await ambiguous.find_transaction("AST-TEST-3")
    assert found is not None and found.status == "SUCCESSFUL"

    rejected = MockGiftCardSupplier(scenario="reject")
    with pytest.raises(SupplierRejectedError):
        await rejected.order(
            product_id="900001",
            unit_price=Decimal("10"),
            custom_identifier="AST-TEST-4",
            sender_name="Architect Store",
        )


def test_supplier_factory_selects_mock_and_keeps_reloadly_compatibility() -> None:
    mock_settings = Settings(giftcard_provider="mock", app_env="test")
    supplier = build_giftcard_supplier(mock_settings)
    assert isinstance(supplier, MockGiftCardSupplier)
    assert configured_giftcard_supplier_identity(mock_settings) == ("mock", "sandbox")

    legacy_settings = Settings(
        giftcard_provider="none",
        reloadly_enabled=True,
        reloadly_environment="sandbox",
    )
    assert configured_giftcard_supplier_identity(legacy_settings) == (
        "reloadly",
        "sandbox",
    )


@pytest.mark.asyncio
async def test_seed_mock_products_is_idempotent_and_visible(
    clean_database, monkeypatch
) -> None:
    settings = Settings(giftcard_provider="mock", app_env="test")
    monkeypatch.setattr("app.cli.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.catalog.get_settings", lambda: settings)

    await seed_mock_products(argparse.Namespace())
    await seed_mock_products(argparse.Namespace())

    async with SessionFactory() as session:
        assert await session.scalar(select(func.count(Product.id))) == 3
        assert await session.scalar(select(func.count(SupplierOffer.id))) == 3
    catalog = await list_catalog()
    assert len(catalog) == 3
    assert all(item.stock_count is None for item in catalog)
    assert all(item.name.startswith("[TESTE]") for item in catalog)
