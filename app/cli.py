from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal, InvalidOperation
from pathlib import Path

from aiogram import Bot
from sqlalchemy import func, select

from app.bot.presentation import apply_telegram_brand
from app.db import SessionFactory, create_schema_for_development, dispose_engine
from app.models import (
    DeliveryType,
    Deposit,
    DepositStatus,
    FulfillmentMode,
    LedgerEntryType,
    Order,
    OrderStatus,
    Product,
    StockItem,
    StockStatus,
    SupplierCatalogItem,
    SupplierOffer,
    SupplierOrder,
    SupplierOrderStatus,
    User,
    Wallet,
    WalletEntry,
)
from app.money import format_brl, parse_brl_to_cents
from app.payments.base import PixProviderError
from app.payments.factory import build_pix_provider
from app.security import SecretBox
from app.services.catalog import reconcile_supplier_order
from app.services.payments import (
    DepositMismatch,
    approve_mock_deposit,
    reconcile_provider_order,
)
from app.settings import Settings, get_settings
from app.suppliers.base import (
    GiftCardProduct,
    GiftCardSupplier,
    SupplierError,
    minimum_sale_for_gross_margin,
)
from app.suppliers.factory import (
    build_giftcard_supplier,
    configured_giftcard_supplier_identity,
)
from app.suppliers.mock import MOCK_SEED_PRODUCTS

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")


async def init_db(_args: argparse.Namespace) -> None:
    await create_schema_for_development()
    print("Schema criado. Em produção, prefira: alembic upgrade head")


async def add_product(args: argparse.Namespace) -> None:
    if not SLUG_RE.fullmatch(args.slug):
        raise ValueError("slug deve conter apenas letras minúsculas, números e hífens")
    price_cents = parse_brl_to_cents(args.price)
    if not 1 <= len(args.name.strip()) <= 120:
        raise ValueError("Nome precisa ter entre 1 e 120 caracteres")
    if not 1 <= len(args.description.strip()) <= 600:
        raise ValueError("Descrição precisa ter entre 1 e 600 caracteres")
    async with SessionFactory() as session:
        existing = await session.scalar(select(Product).where(Product.slug == args.slug))
        if existing:
            raise ValueError(f"Produto '{args.slug}' já existe")
        product = Product(
            slug=args.slug,
            name=args.name.strip(),
            description=args.description.strip(),
            price_cents=price_cents,
            delivery_type=DeliveryType(args.delivery_type),
            supplier_reference=args.supplier_reference,
            active=True,
        )
        session.add(product)
        await session.commit()
        print(f"Produto criado: {product.id} · {product.name} · {format_brl(price_cents)}")


async def list_products(_args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Product, func.count(StockItem.id))
                .outerjoin(
                    StockItem,
                    (StockItem.product_id == Product.id)
                    & (StockItem.status == StockStatus.AVAILABLE),
                )
                .group_by(Product.id)
                .order_by(Product.id)
            )
        ).all()
        if not rows:
            print("Nenhum produto cadastrado.")
            return
        for product, available in rows:
            availability = (
                "fornecedor"
                if product.fulfillment_mode == FulfillmentMode.SUPPLIER
                else f"estoque={available}"
            )
            print(
                f"{product.id:>3}  {product.slug:<24}  {format_brl(product.price_cents):>14}  "
                f"{availability}  ativo={product.active}"
            )


async def set_product_active(args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        product = await session.scalar(
            select(Product).where(Product.slug == args.product).with_for_update()
        )
        if product is None:
            raise ValueError(f"Produto '{args.product}' não encontrado")
        product.active = args.state == "on"
        await session.commit()
        print(f"Produto '{product.slug}' ativo={product.active}")


def _read_stock_file(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"Arquivo não encontrado: {path}")
    payloads: list[str] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith('"'):
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON inválido na linha {line_number}") from exc
            if not isinstance(decoded, str):
                raise ValueError(f"A linha {line_number} precisa ser uma string JSON")
            line = decoded
        if len(line) > 3500:
            raise ValueError(f"Entrega longa demais na linha {line_number}")
        payloads.append(line)
    if not payloads:
        raise ValueError("O arquivo não contém itens de estoque")
    return payloads


async def import_stock(args: argparse.Namespace) -> None:
    settings = get_settings()
    box = SecretBox(settings.data_encryption_key)
    payloads = await asyncio.to_thread(_read_stock_file, Path(args.file))
    fingerprints = [box.fingerprint(payload) for payload in payloads]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("O arquivo contém entregas duplicadas")
    async with SessionFactory() as session:
        product = await session.scalar(select(Product).where(Product.slug == args.product))
        if product is None:
            raise ValueError(f"Produto '{args.product}' não encontrado")
        if product.fulfillment_mode != FulfillmentMode.MANUAL:
            raise ValueError("Produtos de fornecedor não recebem estoque manual")
        existing = await session.scalar(
            select(StockItem.id).where(
                StockItem.product_id == product.id,
                StockItem.payload_fingerprint.in_(fingerprints),
            )
        )
        if existing is not None:
            raise ValueError("Ao menos uma entrega já existe no estoque deste produto")
        session.add_all(
            [
                StockItem(
                    product_id=product.id,
                    payload_ciphertext=box.encrypt(payload),
                    payload_fingerprint=fingerprint,
                    status=StockStatus.AVAILABLE,
                )
                for payload, fingerprint in zip(payloads, fingerprints, strict=True)
            ]
        )
        await session.commit()
    print(f"{len(payloads)} itens criptografados e adicionados a '{args.product}'.")


async def approve_mock(args: argparse.Namespace) -> None:
    result = await approve_mock_deposit(uuid.UUID(args.deposit_id))
    print(f"Depósito {result.deposit_id} aprovado; saldo={format_brl(result.balance_cents or 0)}")


async def telegram_brand(_args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.telegram_bot_token == "development-token":
        raise ValueError("TELEGRAM_BOT_TOKEN não foi configurado")
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await apply_telegram_brand(bot, settings)
        identity = await bot.get_me()
        print(
            f"Perfil atualizado: Architect Store · @{identity.username} · "
            "foto, nome, descrição e comandos aplicados."
        )
    finally:
        await bot.session.close()


async def list_review_deposits(_args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        deposits = (
            await session.scalars(
                select(Deposit)
                .where(Deposit.status == DepositStatus.REVIEW)
                .order_by(Deposit.created_at)
            )
        ).all()
        if not deposits:
            print("Nenhum depósito aguardando revisão.")
            return
        for deposit in deposits:
            provider_status = deposit.provider_status or "-"
            provider_detail = deposit.provider_status_detail or "-"
            print(
                f"{deposit.id}  {format_brl(deposit.amount_cents):>14}  "
                f"provider_order={deposit.provider_order_id or '-'}  "
                f"status={provider_status}:{provider_detail}"
            )


async def reconcile_pix(args: argparse.Namespace) -> None:
    settings = get_settings()
    provider = build_pix_provider(settings)
    result = await reconcile_provider_order(args.provider_order_id, provider)
    print(
        f"Conciliação concluída: evento={result.event} alterado={result.changed} "
        f"depósito={result.deposit_id}"
    )


async def adjust_balance(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[+-]?\d+(?:[.,]\d{1,2})?", args.amount.strip()):
        raise ValueError("Use um valor como 10,00 ou -10,00")
    amount_cents = parse_brl_to_cents(args.amount.lstrip("+-"))
    if args.amount.startswith("-"):
        amount_cents *= -1
    if amount_cents == 0:
        raise ValueError("Ajuste não pode ser zero")
    adjustment_id = uuid.uuid4()
    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == args.telegram_id).with_for_update()
        )
        if user is None:
            raise ValueError("Usuário não encontrado")
        wallet = await session.scalar(
            select(Wallet).where(Wallet.user_id == user.id).with_for_update()
        )
        if wallet is None:
            raise ValueError("Carteira não encontrada")
        wallet.balance_cents += amount_cents
        session.add(
            WalletEntry(
                user_id=user.id,
                entry_type=LedgerEntryType.ADJUSTMENT,
                amount_cents=amount_cents,
                balance_after_cents=wallet.balance_cents,
                idempotency_key=f"admin-adjustment:{adjustment_id}",
                reference_type="adjustment",
                reference_id=str(adjustment_id),
                note=args.reason[:255],
            )
        )
        await session.commit()
        print(f"Novo saldo de {args.telegram_id}: {format_brl(wallet.balance_cents)}")


async def refund_order(args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        order = await session.scalar(
            select(Order).where(Order.public_code == args.order.upper()).with_for_update()
        )
        if order is None:
            raise ValueError("Pedido não encontrado")
        if order.status == OrderStatus.REFUNDED:
            print(f"Pedido {order.public_code} já estava reembolsado.")
            return
        supplier_order = await session.scalar(
            select(SupplierOrder).where(SupplierOrder.order_id == order.id)
        )
        if supplier_order is not None and not args.confirm_supplier_loss:
            raise ValueError(
                "Pedido comprado em fornecedor. Confira a transação externa e repita com "
                "--confirm-supplier-loss se deseja assumir o custo do código"
            )
        wallet = await session.scalar(
            select(Wallet).where(Wallet.user_id == order.user_id).with_for_update()
        )
        if wallet is None:
            raise ValueError("Carteira não encontrada")
        wallet.balance_cents += order.unit_price_cents
        session.add(
            WalletEntry(
                user_id=order.user_id,
                entry_type=LedgerEntryType.REFUND,
                amount_cents=order.unit_price_cents,
                balance_after_cents=wallet.balance_cents,
                idempotency_key=f"order-refund:{order.id}",
                reference_type="order",
                reference_id=str(order.id),
                note=f"Reembolso: {args.reason[:220]}",
            )
        )
        order.status = OrderStatus.REFUNDED
        if supplier_order is not None:
            supplier_order.status = SupplierOrderStatus.REVIEW
            supplier_order.provider_detail = (
                f"Reembolso administrativo assumindo custo: {args.reason[:420]}"
            )
            supplier_order.last_reconciled_at = datetime.now(UTC)
        await session.commit()
        print(f"Pedido {order.public_code} reembolsado em {format_brl(order.unit_price_cents)}.")


def _require_supplier(
    expected_name: str | None = None,
) -> tuple[GiftCardSupplier, Settings]:
    settings = get_settings()
    identity = configured_giftcard_supplier_identity(settings)
    if identity is None:
        raise ValueError(
            "Defina GIFTCARD_PROVIDER=mock para testes ou =reloadly para a API real"
        )
    if expected_name is not None and identity[0] != expected_name:
        raise ValueError(
            f"Este comando exige GIFTCARD_PROVIDER={expected_name}; "
            f"o fornecedor atual é {identity[0]}"
        )
    supplier = build_giftcard_supplier(settings)
    if supplier is None:  # Protege a tipagem caso a fábrica seja alterada.
        raise ValueError("Fornecedor automático não está configurado")
    return supplier, settings


def _decimal_arg(value: str, label: str) -> Decimal:
    try:
        result = Decimal(value.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"{label} inválido") from exc
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -4:
        raise ValueError(f"{label} precisa ser positivo e ter no máximo 4 casas decimais")
    return result


def _product_sender_cost(product: GiftCardProduct, denomination: Decimal) -> Decimal | None:
    return product.estimated_sender_cost(denomination)


def _validate_denomination(product: GiftCardProduct, denomination: Decimal) -> None:
    if product.denomination_type == "FIXED":
        if denomination not in product.fixed_recipient_denominations:
            available = ", ".join(str(value) for value in product.fixed_recipient_denominations)
            raise ValueError(f"Denominação indisponível. Valores válidos: {available}")
        return
    if product.denomination_type == "RANGE":
        minimum = product.min_recipient_denomination
        maximum = product.max_recipient_denomination
        if minimum is None or maximum is None or not minimum <= denomination <= maximum:
            raise ValueError(f"Denominação precisa ficar entre {minimum} e {maximum}")
        return
    raise ValueError(f"Tipo de denominação não suportado: {product.denomination_type}")


async def reloadly_balance(_args: argparse.Namespace) -> None:
    supplier, _settings = _require_supplier("reloadly")
    try:
        balance = await supplier.get_balance()
        print(
            f"Reloadly {supplier.environment}: saldo={balance.amount} {balance.currency}"
        )
    finally:
        await supplier.aclose()


async def reloadly_sync(args: argparse.Namespace) -> None:
    supplier, _settings = _require_supplier("reloadly")
    country = args.country.upper()
    try:
        products = await supplier.list_products(country)
        async with SessionFactory() as session:
            for product in products:
                item = await session.scalar(
                    select(SupplierCatalogItem).where(
                        SupplierCatalogItem.supplier == supplier.name,
                        SupplierCatalogItem.environment == supplier.environment,
                        SupplierCatalogItem.external_product_id == product.product_id,
                        SupplierCatalogItem.country_code == country,
                    )
                )
                if item is None:
                    item = SupplierCatalogItem(
                        supplier=supplier.name,
                        environment=supplier.environment,
                        external_product_id=product.product_id,
                        country_code=country,
                        name=product.name,
                        status=product.status,
                        denomination_type=product.denomination_type,
                        recipient_currency=product.recipient_currency,
                        sender_currency=product.sender_currency,
                        product_data=product.raw,
                    )
                    session.add(item)
                else:
                    item.name = product.name
                    item.status = product.status
                    item.denomination_type = product.denomination_type
                    item.recipient_currency = product.recipient_currency
                    item.sender_currency = product.sender_currency
                    item.product_data = product.raw
                    item.synced_at = datetime.now(UTC)
            await session.commit()
        print(
            f"Catálogo Reloadly {supplier.environment}/{country}: "
            f"{len(products)} produtos sincronizados."
        )
    finally:
        await supplier.aclose()


async def reloadly_catalog(args: argparse.Namespace) -> None:
    supplier, _settings = _require_supplier("reloadly")
    try:
        statement = select(SupplierCatalogItem).where(
            SupplierCatalogItem.supplier == supplier.name,
            SupplierCatalogItem.environment == supplier.environment,
            SupplierCatalogItem.country_code == args.country.upper(),
        )
        if args.search:
            statement = statement.where(
                SupplierCatalogItem.name.ilike(f"%{args.search.strip()}%")
            )
        statement = statement.order_by(SupplierCatalogItem.name).limit(args.limit)
        async with SessionFactory() as session:
            products = list(await session.scalars(statement))
        if not products:
            print("Nenhum item no cache. Execute reloadly-sync primeiro.")
            return
        for product in products:
            values = product.product_data.get("fixedRecipientDenominations") or []
            if product.denomination_type == "RANGE":
                values_text = (
                    f"{product.product_data.get('minRecipientDenomination')}.."
                    f"{product.product_data.get('maxRecipientDenomination')}"
                )
            else:
                values_text = ",".join(str(value) for value in values[:8])
                if len(values) > 8:
                    values_text += ",..."
            print(
                f"id={product.external_product_id:<7} {product.name[:48]:<48} "
                f"{product.recipient_currency} [{values_text}] status={product.status}"
            )
    finally:
        await supplier.aclose()


async def add_reloadly_product(args: argparse.Namespace) -> None:
    if not SLUG_RE.fullmatch(args.slug):
        raise ValueError("slug deve conter apenas letras minúsculas, números e hífens")
    denomination = _decimal_arg(args.denomination, "Denominação")
    price_cents = parse_brl_to_cents(args.sale_price)
    supplier, settings = _require_supplier("reloadly")
    country = args.country.upper()
    try:
        products = await supplier.list_products(country)
        supplier_product = next(
            (product for product in products if product.product_id == args.product_id), None
        )
        if supplier_product is None:
            raise ValueError(
                f"Produto Reloadly {args.product_id} não está disponível em {country}"
            )
        if supplier_product.status != "ACTIVE":
            raise ValueError(f"Produto Reloadly está com status {supplier_product.status}")
        if supplier_product.user_id_required:
            raise ValueError(
                "Este produto exige ID do usuário final; o fluxo interativo ainda "
                "não está habilitado"
            )
        _validate_denomination(supplier_product, denomination)
        if supplier_product.sender_currency != "BRL":
            raise ValueError(
                "O fluxo automático exige uma conta Reloadly em BRL para calcular "
                f"a margem; este custo está em {supplier_product.sender_currency}"
            )
        sender_cost = _product_sender_cost(supplier_product, denomination)
        if sender_cost is None:
            raise ValueError(
                "A Reloadly não informou um custo fixo verificável para esta denominação"
            )
        minimum_sale = minimum_sale_for_gross_margin(
            sender_cost, settings.min_supplier_gross_margin_bps
        )
        minimum_cents = (minimum_sale * Decimal(100)).quantize(
            Decimal(1), rounding=ROUND_UP
        )
        if Decimal(price_cents) < minimum_cents:
            raise ValueError(
                "Venda abaixo da margem mínima: use pelo menos "
                f"R$ {minimum_cents / Decimal(100):.2f}"
            )
        name = (args.name or f"{supplier_product.name} {denomination}").strip()
        description = (
            args.description
            or (
                f"Gift card autorizado de {denomination} "
                f"{supplier_product.recipient_currency}. Entrega automática."
            )
        ).strip()
        if not 1 <= len(name) <= 120:
            raise ValueError("Nome precisa ter entre 1 e 120 caracteres")
        if not 1 <= len(description) <= 600:
            raise ValueError("Descrição precisa ter entre 1 e 600 caracteres")

        async with SessionFactory() as session:
            if await session.scalar(select(Product.id).where(Product.slug == args.slug)):
                raise ValueError(f"Produto '{args.slug}' já existe")
            existing_offer = await session.scalar(
                select(SupplierOffer.id).where(
                    SupplierOffer.supplier == supplier.name,
                    SupplierOffer.environment == supplier.environment,
                    SupplierOffer.external_product_id == supplier_product.product_id,
                    SupplierOffer.country_code == country,
                    SupplierOffer.unit_price == denomination,
                )
            )
            if existing_offer:
                raise ValueError("Esta oferta Reloadly já foi vinculada a outro produto")
            product = Product(
                slug=args.slug,
                name=name,
                description=description,
                price_cents=price_cents,
                delivery_type=DeliveryType.CODE,
                fulfillment_mode=FulfillmentMode.SUPPLIER,
                supplier_reference=(
                    f"reloadly:{supplier.environment}:{supplier_product.product_id}:{denomination}"
                ),
                active=args.activate,
            )
            session.add(product)
            await session.flush()
            session.add(
                SupplierOffer(
                    product_id=product.id,
                    supplier=supplier.name,
                    environment=supplier.environment,
                    external_product_id=supplier_product.product_id,
                    country_code=country,
                    unit_price=denomination,
                    recipient_currency=supplier_product.recipient_currency,
                    sender_currency=supplier_product.sender_currency,
                    estimated_sender_cost=sender_cost,
                    discount_percentage=supplier_product.discount_percentage,
                    active=True,
                )
            )
            await session.commit()
        cost_text = f"{sender_cost} {supplier_product.sender_currency}"
        print(
            f"Produto criado: {product.slug} · venda={format_brl(price_cents)} · "
            f"custo estimado={cost_text} · ativo={product.active}"
        )
    finally:
        await supplier.aclose()


async def mock_catalog(_args: argparse.Namespace) -> None:
    supplier, _settings = _require_supplier("mock")
    try:
        products = await supplier.list_products("BR")
        print("Catálogo do fornecedor mock (somente teste; nenhum item tem valor real):")
        for product in products:
            values = ", ".join(
                f"R$ {value:.2f}" for value in product.fixed_recipient_denominations
            )
            print(f"id={product.product_id}  {product.name}  valores=[{values}]")
    finally:
        await supplier.aclose()


async def seed_mock_products(_args: argparse.Namespace) -> None:
    supplier, _settings = _require_supplier("mock")
    created = 0
    updated = 0
    try:
        async with SessionFactory() as session:
            for seed in MOCK_SEED_PRODUCTS:
                supplier_product = await supplier.get_product(seed.external_product_id)
                sender_cost = supplier_product.estimated_sender_cost(seed.denomination)
                if sender_cost is None:
                    raise ValueError(f"Custo mock indisponível para {seed.slug}")

                product = await session.scalar(
                    select(Product).where(Product.slug == seed.slug)
                )
                if product is None:
                    product = Product(
                        slug=seed.slug,
                        name=seed.name,
                        description=seed.description,
                        price_cents=seed.sale_price_cents,
                        delivery_type=DeliveryType.CODE,
                        fulfillment_mode=FulfillmentMode.SUPPLIER,
                        supplier_reference=(
                            f"mock:sandbox:{seed.external_product_id}:"
                            f"{seed.denomination}"
                        ),
                        active=True,
                    )
                    session.add(product)
                    await session.flush()
                    created += 1
                else:
                    if not (product.supplier_reference or "").startswith("mock:"):
                        raise ValueError(
                            f"O slug reservado '{seed.slug}' pertence a outro produto"
                        )
                    product.name = seed.name
                    product.description = seed.description
                    product.price_cents = seed.sale_price_cents
                    product.delivery_type = DeliveryType.CODE
                    product.fulfillment_mode = FulfillmentMode.SUPPLIER
                    product.active = True
                    updated += 1

                offer = await session.scalar(
                    select(SupplierOffer).where(
                        SupplierOffer.supplier == supplier.name,
                        SupplierOffer.environment == supplier.environment,
                        SupplierOffer.external_product_id == seed.external_product_id,
                        SupplierOffer.country_code == "BR",
                        SupplierOffer.unit_price == seed.denomination,
                    )
                )
                if offer is None:
                    offer = SupplierOffer(
                        product_id=product.id,
                        supplier=supplier.name,
                        environment=supplier.environment,
                        external_product_id=seed.external_product_id,
                        country_code="BR",
                        unit_price=seed.denomination,
                        recipient_currency="BRL",
                        sender_currency="BRL",
                        estimated_sender_cost=sender_cost,
                        discount_percentage=None,
                        active=True,
                    )
                    session.add(offer)
                elif offer.product_id != product.id:
                    raise ValueError(
                        f"A oferta mock de '{seed.slug}' pertence a outro produto"
                    )
                else:
                    offer.estimated_sender_cost = sender_cost
                    offer.active = True
            await session.commit()
        print(
            f"Produtos mock prontos: criados={created}, atualizados={updated}. "
            "Todos são fictícios e estão ativos no catálogo de teste."
        )
    finally:
        await supplier.aclose()


async def list_review_supplier_orders(_args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Order, SupplierOrder, Product)
                .join(SupplierOrder, SupplierOrder.order_id == Order.id)
                .join(Product, Product.id == Order.product_id)
                .where(
                    SupplierOrder.status.in_(
                        [SupplierOrderStatus.RESERVED, SupplierOrderStatus.REVIEW]
                    ),
                    Order.status.in_([OrderStatus.PROCESSING, OrderStatus.REVIEW]),
                )
                .order_by(SupplierOrder.created_at)
            )
        ).all()
    if not rows:
        print("Nenhum pedido de fornecedor aguardando revisão.")
        return
    for order, supplier_order, product in rows:
        print(
            f"{order.public_code}  {product.name[:35]:<35} "
            f"provider={supplier_order.provider_status or '-'} "
            f"tx={supplier_order.external_transaction_id or '-'} "
            f"detalhe={supplier_order.provider_detail or '-'}"
        )


async def reconcile_supplier(args: argparse.Namespace) -> None:
    supplier, _settings = _require_supplier()
    try:
        result = await reconcile_supplier_order(args.order, supplier)
        print(
            f"Pedido {result.public_code}: status={result.status.value} "
            f"saldo={format_brl(result.balance_cents)} "
            f"entrega={'sim' if result.delivery else 'não'}"
        )
    finally:
        await supplier.aclose()


async def set_user_blocked(args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == args.telegram_id).with_for_update()
        )
        if user is None:
            raise ValueError("Usuário não encontrado")
        user.is_blocked = args.state == "on"
        await session.commit()
        print(f"Usuário {user.telegram_id} bloqueado={user.is_blocked}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Administração da Architect Store")
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("init-db", help="criar schema somente em desenvolvimento")
    command.set_defaults(handler=init_db)

    command = subparsers.add_parser("add-product", help="cadastrar produto")
    command.add_argument("--slug", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--price", required=True, help="ex.: 29,90")
    command.add_argument("--description", required=True)
    command.add_argument(
        "--delivery-type",
        choices=[item.value for item in DeliveryType],
        default=DeliveryType.CODE.value,
    )
    command.add_argument("--supplier-reference", required=True)
    command.set_defaults(handler=add_product)

    command = subparsers.add_parser("list-products", help="listar catálogo e estoque")
    command.set_defaults(handler=list_products)

    command = subparsers.add_parser("set-product-active", help="ativar ou pausar produto")
    command.add_argument("--product", required=True, help="slug do produto")
    command.add_argument("--state", choices=["on", "off"], required=True)
    command.set_defaults(handler=set_product_active)

    command = subparsers.add_parser("import-stock", help="importar uma entrega por linha")
    command.add_argument("--product", required=True, help="slug do produto")
    command.add_argument("--file", required=True)
    command.set_defaults(handler=import_stock)

    command = subparsers.add_parser("approve-mock", help="aprovar depósito no modo de teste")
    command.add_argument("deposit_id")
    command.set_defaults(handler=approve_mock)

    command = subparsers.add_parser(
        "telegram-brand", help="aplicar nome, descrições, comandos e avatar no Telegram"
    )
    command.set_defaults(handler=telegram_brand)

    command = subparsers.add_parser(
        "list-review-deposits", help="listar depósitos que exigem revisão"
    )
    command.set_defaults(handler=list_review_deposits)

    command = subparsers.add_parser("reconcile-pix", help="consultar e conciliar uma Order")
    command.add_argument("provider_order_id")
    command.set_defaults(handler=reconcile_pix)

    command = subparsers.add_parser("adjust-balance", help="ajuste manual auditável")
    command.add_argument("--telegram-id", required=True, type=int)
    command.add_argument("--amount", required=True, help="ex.: 10,00 ou -10,00")
    command.add_argument("--reason", required=True)
    command.set_defaults(handler=adjust_balance)

    command = subparsers.add_parser("refund-order", help="reembolsar pedido em crédito")
    command.add_argument("--order", required=True, help="código PED-XXXXXXXX")
    command.add_argument("--reason", required=True)
    command.add_argument(
        "--confirm-supplier-loss",
        action="store_true",
        help="confirmar reembolso de item já comprado em fornecedor",
    )
    command.set_defaults(handler=refund_order)

    command = subparsers.add_parser(
        "mock-catalog", help="mostrar o catálogo fictício do fornecedor mock"
    )
    command.set_defaults(handler=mock_catalog)

    command = subparsers.add_parser(
        "seed-mock-products", help="criar ou atualizar os produtos fictícios de teste"
    )
    command.set_defaults(handler=seed_mock_products)

    command = subparsers.add_parser(
        "reloadly-balance", help="consultar carteira Reloadly do ambiente configurado"
    )
    command.set_defaults(handler=reloadly_balance)

    command = subparsers.add_parser(
        "reloadly-sync", help="sincronizar catálogo Reloadly de um país"
    )
    command.add_argument("--country", default="BR")
    command.set_defaults(handler=reloadly_sync)

    command = subparsers.add_parser(
        "reloadly-catalog", help="pesquisar catálogo Reloadly sincronizado"
    )
    command.add_argument("--country", default="BR")
    command.add_argument("--search")
    command.add_argument("--limit", type=int, default=30, choices=range(1, 101))
    command.set_defaults(handler=reloadly_catalog)

    command = subparsers.add_parser(
        "add-reloadly-product", help="vincular produto local a uma oferta Reloadly"
    )
    command.add_argument("--slug", required=True)
    command.add_argument("--product-id", required=True)
    command.add_argument("--country", default="BR")
    command.add_argument("--denomination", required=True, help="valor na moeda do gift card")
    command.add_argument("--sale-price", required=True, help="preço de venda em BRL")
    command.add_argument("--name")
    command.add_argument("--description")
    command.add_argument(
        "--activate",
        action="store_true",
        help="exibir imediatamente no catálogo (por padrão nasce pausado)",
    )
    command.set_defaults(handler=add_reloadly_product)

    command = subparsers.add_parser(
        "list-review-supplier-orders", help="listar pedidos externos que exigem revisão"
    )
    command.set_defaults(handler=list_review_supplier_orders)

    command = subparsers.add_parser(
        "reconcile-supplier-order", help="conciliar um pedido com o fornecedor ativo"
    )
    command.add_argument("--order", required=True, help="código PED-XXXXXXXX")
    command.set_defaults(handler=reconcile_supplier)

    command = subparsers.add_parser("block-user", help="bloquear ou liberar compras")
    command.add_argument("--telegram-id", required=True, type=int)
    command.add_argument("--state", choices=["on", "off"], required=True)
    command.set_defaults(handler=set_user_blocked)
    return parser


async def _run(args: argparse.Namespace) -> None:
    try:
        await args.handler(args)
    finally:
        await dispose_engine()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(args))
    except (ValueError, LookupError, PixProviderError, DepositMismatch, SupplierError) as exc:
        raise SystemExit(f"Erro: {exc}") from exc


if __name__ == "__main__":
    main()
