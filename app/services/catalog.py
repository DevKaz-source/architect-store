from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionFactory
from app.models import (
    FulfillmentMode,
    LedgerEntryType,
    Order,
    OrderStatus,
    Product,
    StockItem,
    StockStatus,
    SupplierOffer,
    SupplierOrder,
    SupplierOrderStatus,
    User,
    Wallet,
    WalletEntry,
)
from app.security import SecretBox
from app.settings import get_settings
from app.suppliers.base import (
    GiftCardSupplier,
    SupplierAmbiguousError,
    SupplierError,
    SupplierTransaction,
    minimum_sale_for_gross_margin,
)
from app.suppliers.factory import configured_giftcard_supplier_identity


class PurchaseError(RuntimeError):
    pass


class TermsRequired(PurchaseError):
    pass


class UserBlocked(PurchaseError):
    pass


class ProductUnavailable(PurchaseError):
    pass


class OutOfStock(PurchaseError):
    pass


class InsufficientBalance(PurchaseError):
    def __init__(self, *, balance_cents: int, price_cents: int) -> None:
        self.balance_cents = balance_cents
        self.price_cents = price_cents
        super().__init__("Saldo insuficiente")


@dataclass(frozen=True)
class CatalogItem:
    id: int
    name: str
    description: str
    price_cents: int
    stock_count: int | None

    @property
    def availability_label(self) -> str:
        if self.stock_count is None:
            return "entrega automática"
        return f"{self.stock_count} em estoque"


@dataclass(frozen=True)
class PurchaseResult:
    order_id: uuid.UUID
    public_code: str
    product_name: str
    delivery: str | None
    balance_cents: int
    status: OrderStatus
    repeated: bool = False


@dataclass(frozen=True)
class OrderSummary:
    id: uuid.UUID
    public_code: str
    product_name: str
    price_cents: int
    status: OrderStatus
    created_at: datetime


@dataclass(frozen=True)
class SupplierNotification:
    telegram_id: int
    result: PurchaseResult


def _box() -> SecretBox:
    return SecretBox(get_settings().data_encryption_key)


def _decrypt_delivery(order: Order, stock: StockItem | None) -> str | None:
    ciphertext = order.delivery_ciphertext
    if ciphertext is None and stock is not None:
        ciphertext = stock.payload_ciphertext
    return _box().decrypt(ciphertext) if ciphertext is not None else None


def _purchase_result(
    *,
    order: Order,
    product: Product,
    stock: StockItem | None,
    balance_cents: int,
    repeated: bool,
) -> PurchaseResult:
    return PurchaseResult(
        order_id=order.id,
        public_code=order.public_code,
        product_name=product.name,
        delivery=_decrypt_delivery(order, stock),
        balance_cents=balance_cents,
        status=order.status,
        repeated=repeated,
    )


async def _result_by_order_id(order_id: uuid.UUID, *, repeated: bool) -> PurchaseResult:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(Order, Product, StockItem, Wallet)
                .join(Product, Product.id == Order.product_id)
                .outerjoin(StockItem, StockItem.id == Order.stock_item_id)
                .join(Wallet, Wallet.user_id == Order.user_id)
                .where(Order.id == order_id)
            )
        ).first()
        if row is None:
            raise LookupError("Pedido não encontrado")
        order, product, stock, wallet = row
        return _purchase_result(
            order=order,
            product=product,
            stock=stock,
            balance_cents=wallet.balance_cents,
            repeated=repeated,
        )


async def _find_repeated(
    session: AsyncSession, *, telegram_id: int, idempotency_key: str
) -> PurchaseResult | None:
    row = (
        await session.execute(
            select(Order, Product, StockItem, Wallet)
            .join(User, User.id == Order.user_id)
            .join(Product, Product.id == Order.product_id)
            .outerjoin(StockItem, StockItem.id == Order.stock_item_id)
            .join(Wallet, Wallet.user_id == Order.user_id)
            .where(
                Order.idempotency_key == idempotency_key,
                User.telegram_id == telegram_id,
            )
        )
    ).first()
    if row is None:
        return None
    order, product, stock, wallet = row
    return _purchase_result(
        order=order,
        product=product,
        stock=stock,
        balance_cents=wallet.balance_cents,
        repeated=True,
    )


async def list_catalog() -> list[CatalogItem]:
    settings = get_settings()
    supplier_identity = configured_giftcard_supplier_identity(settings)
    offer_match = false()
    supplier_available = false()
    if supplier_identity is not None:
        supplier_name, supplier_environment = supplier_identity
        offer_match = and_(
            SupplierOffer.product_id == Product.id,
            SupplierOffer.active.is_(True),
            SupplierOffer.supplier == supplier_name,
            SupplierOffer.environment == supplier_environment,
        )
        supplier_available = SupplierOffer.id.is_not(None)
    async with SessionFactory() as session:
        statement = (
            select(
                Product,
                func.count(func.distinct(StockItem.id)),
                func.count(func.distinct(SupplierOffer.id)),
            )
            .outerjoin(
                StockItem,
                and_(
                    StockItem.product_id == Product.id,
                    StockItem.status == StockStatus.AVAILABLE,
                ),
            )
            .outerjoin(SupplierOffer, offer_match)
            .where(
                Product.active.is_(True),
                or_(
                    Product.fulfillment_mode == FulfillmentMode.MANUAL,
                    and_(
                        Product.fulfillment_mode == FulfillmentMode.SUPPLIER,
                        supplier_available,
                    ),
                ),
            )
            .group_by(Product.id)
            .order_by(Product.name)
        )
        rows = (await session.execute(statement)).all()
        return [
            CatalogItem(
                id=product.id,
                name=product.name,
                description=product.description,
                price_cents=product.price_cents,
                stock_count=None
                if product.fulfillment_mode == FulfillmentMode.SUPPLIER and int(offer_count) > 0
                else int(stock_count),
            )
            for product, stock_count, offer_count in rows
        ]


async def get_catalog_item(product_id: int) -> CatalogItem | None:
    items = await list_catalog()
    return next((item for item in items if item.id == product_id), None)


def _add_purchase_ledger(
    session: AsyncSession, *, user: User, wallet: Wallet, order: Order, product: Product
) -> None:
    wallet.balance_cents -= product.price_cents
    session.add(
        WalletEntry(
            user_id=user.id,
            entry_type=LedgerEntryType.PURCHASE,
            amount_cents=-product.price_cents,
            balance_after_cents=wallet.balance_cents,
            idempotency_key=f"purchase:{order.id}",
            reference_type="order",
            reference_id=str(order.id),
            note=f"Compra de {product.name}",
        )
    )


async def _mark_supplier_review(order_id: uuid.UUID, detail: str) -> PurchaseResult:
    async with SessionFactory() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        supplier_order = await session.scalar(
            select(SupplierOrder).where(SupplierOrder.order_id == order_id).with_for_update()
        )
        if order is None or supplier_order is None:
            raise LookupError("Pedido do fornecedor não encontrado")
        if order.status not in {OrderStatus.DELIVERED, OrderStatus.REFUNDED}:
            order.status = OrderStatus.REVIEW
            supplier_order.status = SupplierOrderStatus.REVIEW
            supplier_order.provider_detail = detail[:500]
            supplier_order.last_reconciled_at = datetime.now(UTC)
        await session.commit()
    return await _result_by_order_id(order_id, repeated=False)


async def _pause_product_for_order(order_id: uuid.UUID) -> None:
    async with SessionFactory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            return
        product = await session.scalar(
            select(Product).where(Product.id == order.product_id).with_for_update()
        )
        if product is not None:
            product.active = False
            await session.commit()


async def _refund_supplier_order(
    order_id: uuid.UUID, *, detail: str, provider_status: str | None = None
) -> PurchaseResult:
    delivered_conflict = False
    async with SessionFactory() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        supplier_order = await session.scalar(
            select(SupplierOrder).where(SupplierOrder.order_id == order_id).with_for_update()
        )
        if order is None or supplier_order is None:
            raise LookupError("Pedido do fornecedor não encontrado")
        if order.status == OrderStatus.DELIVERED:
            supplier_order.status = SupplierOrderStatus.REVIEW
            supplier_order.provider_detail = (
                "Fornecedor indicou estorno após entrega; revisão manual"
            )
            delivered_conflict = True
        else:
            if order.status != OrderStatus.REFUNDED:
                wallet = await session.scalar(
                    select(Wallet).where(Wallet.user_id == order.user_id).with_for_update()
                )
                if wallet is None:
                    raise LookupError("Carteira não encontrada")
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
                        note="Reembolso automático: fornecedor não concluiu a compra",
                    )
                )
                order.status = OrderStatus.REFUNDED
            supplier_order.status = SupplierOrderStatus.REJECTED
            supplier_order.provider_status = provider_status or supplier_order.provider_status
            supplier_order.provider_detail = detail[:500]
        supplier_order.last_reconciled_at = datetime.now(UTC)
        await session.commit()
    return await _result_by_order_id(order_id, repeated=delivered_conflict)


def _transaction_matches(
    transaction: SupplierTransaction,
    *,
    supplier_order: SupplierOrder,
    offer: SupplierOffer,
) -> bool:
    return (
        transaction.custom_identifier == supplier_order.custom_identifier
        and transaction.product_id == offer.external_product_id
        and transaction.unit_price == Decimal(str(offer.unit_price))
        and transaction.quantity == 1
    )


async def _record_transaction(order_id: uuid.UUID, transaction: SupplierTransaction) -> bool:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(SupplierOrder, SupplierOffer)
                .join(SupplierOffer, SupplierOffer.id == SupplierOrder.offer_id)
                .where(SupplierOrder.order_id == order_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            raise LookupError("Pedido do fornecedor não encontrado")
        supplier_order, offer = row
        if not _transaction_matches(transaction, supplier_order=supplier_order, offer=offer):
            supplier_order.status = SupplierOrderStatus.REVIEW
            supplier_order.provider_detail = "Transação divergente retornada pelo fornecedor"
            supplier_order.last_reconciled_at = datetime.now(UTC)
            order = await session.get(Order, order_id)
            if order is not None and order.status != OrderStatus.DELIVERED:
                order.status = OrderStatus.REVIEW
            await session.commit()
            return False
        supplier_order.external_transaction_id = transaction.transaction_id
        supplier_order.provider_status = transaction.status
        supplier_order.provider_amount = transaction.amount
        supplier_order.provider_currency = transaction.currency
        supplier_order.submitted_at = supplier_order.submitted_at or datetime.now(UTC)
        supplier_order.last_reconciled_at = datetime.now(UTC)
        if transaction.status in {"PENDING", "PROCESSING", "SUCCESSFUL"}:
            supplier_order.status = SupplierOrderStatus.SUBMITTED
        await session.commit()
        return True


async def _deliver_supplier_code(
    order_id: uuid.UUID, *, supplier: GiftCardSupplier, transaction_id: str
) -> PurchaseResult:
    try:
        code = await supplier.get_redeem_code(transaction_id)
        delivery = code.as_delivery()
    except SupplierError as exc:
        async with SessionFactory() as session:
            supplier_order = await session.scalar(
                select(SupplierOrder)
                .where(SupplierOrder.order_id == order_id)
                .with_for_update()
            )
            if supplier_order is not None:
                supplier_order.status = SupplierOrderStatus.SUBMITTED
                supplier_order.provider_detail = f"Código ainda indisponível: {exc}"[:500]
                supplier_order.last_reconciled_at = datetime.now(UTC)
                await session.commit()
        return await _result_by_order_id(order_id, repeated=False)

    async with SessionFactory() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        supplier_order = await session.scalar(
            select(SupplierOrder).where(SupplierOrder.order_id == order_id).with_for_update()
        )
        if order is None or supplier_order is None:
            raise LookupError("Pedido do fornecedor não encontrado")
        if order.status != OrderStatus.REFUNDED:
            order.delivery_ciphertext = order.delivery_ciphertext or _box().encrypt(delivery)
            order.status = OrderStatus.DELIVERED
            order.delivered_at = order.delivered_at or datetime.now(UTC)
            supplier_order.status = SupplierOrderStatus.DELIVERED
            supplier_order.fulfilled_at = supplier_order.fulfilled_at or datetime.now(UTC)
            supplier_order.provider_detail = None
        else:
            supplier_order.status = SupplierOrderStatus.REVIEW
            supplier_order.provider_detail = "Código recebido depois do reembolso; revisão manual"
        supplier_order.last_reconciled_at = datetime.now(UTC)
        await session.commit()
    return await _result_by_order_id(order_id, repeated=False)


async def _apply_supplier_transaction(
    order_id: uuid.UUID,
    *,
    supplier: GiftCardSupplier,
    transaction: SupplierTransaction,
) -> PurchaseResult:
    if not await _record_transaction(order_id, transaction):
        return await _result_by_order_id(order_id, repeated=False)
    status = transaction.status
    if status == "SUCCESSFUL":
        return await _deliver_supplier_code(
            order_id,
            supplier=supplier,
            transaction_id=transaction.transaction_id,
        )
    if status == "REFUNDED":
        return await _refund_supplier_order(
            order_id,
            detail="O fornecedor informou que a transação foi estornada",
            provider_status=status,
        )
    if status in {"PENDING", "PROCESSING"}:
        return await _result_by_order_id(order_id, repeated=False)
    return await _mark_supplier_review(
        order_id,
        f"Status {status} exige conciliação antes de reembolsar ou repetir",
    )


async def _fulfill_supplier_purchase(
    *,
    order_id: uuid.UUID,
    supplier: GiftCardSupplier,
    offer: SupplierOffer,
    custom_identifier: str,
    sale_price_cents: int,
) -> PurchaseResult:
    settings = get_settings()
    unit_price = Decimal(str(offer.unit_price))
    try:
        live_product = await supplier.get_product(offer.external_product_id)
    except SupplierError as exc:
        return await _refund_supplier_order(
            order_id, detail=f"Falha na pré-validação do produto: {exc}"
        )
    denomination_valid = (
        unit_price in live_product.fixed_recipient_denominations
        if live_product.denomination_type == "FIXED"
        else (
            live_product.denomination_type == "RANGE"
            and live_product.min_recipient_denomination is not None
            and live_product.max_recipient_denomination is not None
            and live_product.min_recipient_denomination
            <= unit_price
            <= live_product.max_recipient_denomination
        )
    )
    if (
        live_product.status != "ACTIVE"
        or live_product.user_id_required
        or not denomination_valid
    ):
        await _pause_product_for_order(order_id)
        return await _refund_supplier_order(
            order_id,
            detail="Produto ou denominação deixou de estar disponível no fornecedor",
        )
    live_cost = live_product.estimated_sender_cost(unit_price)
    if live_product.sender_currency != "BRL" or live_cost is None:
        await _pause_product_for_order(order_id)
        return await _refund_supplier_order(
            order_id,
            detail="Custo em BRL não pôde ser confirmado; produto pausado automaticamente",
        )
    sale_brl = Decimal(sale_price_cents) / Decimal(100)
    minimum_sale = minimum_sale_for_gross_margin(
        live_cost, settings.min_supplier_gross_margin_bps
    )
    if sale_brl < minimum_sale:
        await _pause_product_for_order(order_id)
        return await _refund_supplier_order(
            order_id,
            detail="Preço pausado automaticamente porque o custo ultrapassou a margem mínima",
        )
    try:
        transaction = await supplier.order(
            product_id=offer.external_product_id,
            unit_price=unit_price,
            custom_identifier=custom_identifier,
            sender_name=settings.reloadly_sender_name,
        )
    except SupplierAmbiguousError as exc:
        return await _mark_supplier_review(order_id, f"Resultado incerto: {exc}")
    except SupplierError as exc:
        return await _refund_supplier_order(order_id, detail=f"Pedido rejeitado: {exc}")
    return await _apply_supplier_transaction(order_id, supplier=supplier, transaction=transaction)


async def purchase_product(
    *,
    telegram_id: int,
    product_id: int,
    idempotency_key: str,
    supplier: GiftCardSupplier | None = None,
) -> PurchaseResult:
    async with SessionFactory() as session:
        repeated = await _find_repeated(
            session, telegram_id=telegram_id, idempotency_key=idempotency_key
        )
        if repeated:
            return repeated

        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            raise LookupError("Usuário não encontrado")
        if user.is_blocked:
            raise UserBlocked("Conta bloqueada")
        if user.accepted_terms_at is None:
            raise TermsRequired("Aceite os termos antes de comprar")

        repeated = await _find_repeated(
            session, telegram_id=telegram_id, idempotency_key=idempotency_key
        )
        if repeated:
            return repeated

        product = await session.scalar(
            select(Product).where(Product.id == product_id, Product.active.is_(True))
        )
        if product is None:
            raise ProductUnavailable("Produto indisponível")

        wallet = await session.scalar(
            select(Wallet).where(Wallet.user_id == user.id).with_for_update()
        )
        if wallet is None:
            raise LookupError("Carteira não encontrada")
        if wallet.balance_cents < product.price_cents:
            raise InsufficientBalance(
                balance_cents=wallet.balance_cents, price_cents=product.price_cents
            )

        if product.fulfillment_mode == FulfillmentMode.MANUAL:
            stock = await session.scalar(
                select(StockItem)
                .where(
                    StockItem.product_id == product.id,
                    StockItem.status == StockStatus.AVAILABLE,
                )
                .order_by(StockItem.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if stock is None:
                raise OutOfStock("Produto sem estoque")
            order = Order(
                idempotency_key=idempotency_key,
                user_id=user.id,
                product_id=product.id,
                stock_item_id=stock.id,
                unit_price_cents=product.price_cents,
                status=OrderStatus.DELIVERED,
                delivered_at=datetime.now(UTC),
            )
            session.add(order)
            await session.flush()
            stock.status = StockStatus.SOLD
            stock.sold_at = datetime.now(UTC)
            _add_purchase_ledger(session, user=user, wallet=wallet, order=order, product=product)
            result = _purchase_result(
                order=order,
                product=product,
                stock=stock,
                balance_cents=wallet.balance_cents,
                repeated=False,
            )
            await session.commit()
            return result

        if supplier is None:
            raise ProductUnavailable("Fornecedor automático não está configurado")
        offer = await session.scalar(
            select(SupplierOffer)
            .where(
                SupplierOffer.product_id == product.id,
                SupplierOffer.supplier == supplier.name,
                SupplierOffer.environment == supplier.environment,
                SupplierOffer.active.is_(True),
            )
            .order_by(SupplierOffer.estimated_sender_cost.asc().nulls_last())
            .with_for_update()
            .limit(1)
        )
        if offer is None:
            raise ProductUnavailable("Produto não possui oferta ativa neste ambiente")

        order = Order(
            idempotency_key=idempotency_key,
            user_id=user.id,
            product_id=product.id,
            stock_item_id=None,
            unit_price_cents=product.price_cents,
            status=OrderStatus.PROCESSING,
            delivered_at=None,
        )
        session.add(order)
        await session.flush()
        custom_identifier = f"AST-{order.id.hex}"
        session.add(
            SupplierOrder(
                order_id=order.id,
                offer_id=offer.id,
                supplier=supplier.name,
                environment=supplier.environment,
                status=SupplierOrderStatus.RESERVED,
                custom_identifier=custom_identifier,
            )
        )
        _add_purchase_ledger(session, user=user, wallet=wallet, order=order, product=product)
        await session.commit()
        order_id = order.id

    return await _fulfill_supplier_purchase(
        order_id=order_id,
        supplier=supplier,
        offer=offer,
        custom_identifier=custom_identifier,
        sale_price_cents=product.price_cents,
    )


async def reconcile_supplier_order(
    public_code: str, supplier: GiftCardSupplier
) -> PurchaseResult:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(Order, SupplierOrder)
                .join(SupplierOrder, SupplierOrder.order_id == Order.id)
                .where(Order.public_code == public_code.upper())
            )
        ).first()
        if row is None:
            raise LookupError("Pedido de fornecedor não encontrado")
        order, supplier_order = row
        if (
            supplier_order.supplier != supplier.name
            or supplier_order.environment != supplier.environment
        ):
            raise ProductUnavailable("Pedido pertence a outro fornecedor ou ambiente")
        already_finished = order.status in {OrderStatus.DELIVERED, OrderStatus.REFUNDED}
        order_id = order.id
        transaction_id = supplier_order.external_transaction_id
        custom_identifier = supplier_order.custom_identifier

    if already_finished:
        return await _result_by_order_id(order_id, repeated=True)
    try:
        transaction = (
            await supplier.get_transaction(transaction_id)
            if transaction_id
            else await supplier.find_transaction(custom_identifier)
        )
    except SupplierError as exc:
        return await _mark_supplier_review(order_id, f"Conciliação falhou: {exc}")
    if transaction is None:
        return await _mark_supplier_review(
            order_id,
            "O fornecedor ainda não localizou a transação; não repetir nem "
            "reembolsar automaticamente",
        )
    return await _apply_supplier_transaction(order_id, supplier=supplier, transaction=transaction)


async def list_user_orders(telegram_id: int, limit: int = 10) -> list[OrderSummary]:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Order, Product)
                .join(User, User.id == Order.user_id)
                .join(Product, Product.id == Order.product_id)
                .where(User.telegram_id == telegram_id)
                .order_by(Order.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            OrderSummary(
                id=order.id,
                public_code=order.public_code,
                product_name=product.name,
                price_cents=order.unit_price_cents,
                status=order.status,
                created_at=order.created_at,
            )
            for order, product in rows
        ]


async def reveal_order(telegram_id: int, order_id: uuid.UUID) -> PurchaseResult:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(Order, Product, StockItem, Wallet)
                .join(User, User.id == Order.user_id)
                .join(Product, Product.id == Order.product_id)
                .outerjoin(StockItem, StockItem.id == Order.stock_item_id)
                .join(Wallet, Wallet.user_id == User.id)
                .where(User.telegram_id == telegram_id, Order.id == order_id)
            )
        ).first()
        if row is None:
            raise LookupError("Pedido não encontrado")
        order, product, stock, wallet = row
        order.last_viewed_at = datetime.now(UTC)
        result = _purchase_result(
            order=order,
            product=product,
            stock=stock,
            balance_cents=wallet.balance_cents,
            repeated=True,
        )
        await session.commit()
        return result


async def list_reconcilable_supplier_orders(
    supplier: GiftCardSupplier, limit: int = 20
) -> list[str]:
    async with SessionFactory() as session:
        return list(
            await session.scalars(
                select(Order.public_code)
                .join(SupplierOrder, SupplierOrder.order_id == Order.id)
                .where(
                    SupplierOrder.supplier == supplier.name,
                    SupplierOrder.environment == supplier.environment,
                    SupplierOrder.status.in_(
                        [
                            SupplierOrderStatus.RESERVED,
                            SupplierOrderStatus.SUBMITTED,
                            SupplierOrderStatus.REVIEW,
                        ]
                    ),
                    Order.status.in_([OrderStatus.PROCESSING, OrderStatus.REVIEW]),
                )
                .order_by(SupplierOrder.updated_at)
                .limit(limit)
            )
        )


async def list_supplier_notifications(
    supplier: GiftCardSupplier, limit: int = 20
) -> list[SupplierNotification]:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Order, Product, StockItem, Wallet, User)
                .join(SupplierOrder, SupplierOrder.order_id == Order.id)
                .join(Product, Product.id == Order.product_id)
                .outerjoin(StockItem, StockItem.id == Order.stock_item_id)
                .join(Wallet, Wallet.user_id == Order.user_id)
                .join(User, User.id == Order.user_id)
                .where(
                    SupplierOrder.supplier == supplier.name,
                    SupplierOrder.environment == supplier.environment,
                    SupplierOrder.customer_notified_at.is_(None),
                    Order.status.in_([OrderStatus.DELIVERED, OrderStatus.REFUNDED]),
                )
                .order_by(SupplierOrder.updated_at)
                .limit(limit)
            )
        ).all()
        return [
            SupplierNotification(
                telegram_id=user.telegram_id,
                result=_purchase_result(
                    order=order,
                    product=product,
                    stock=stock,
                    balance_cents=wallet.balance_cents,
                    repeated=True,
                ),
            )
            for order, product, stock, wallet, user in rows
        ]


async def mark_supplier_customer_notified(order_id: uuid.UUID) -> None:
    async with SessionFactory() as session:
        supplier_order = await session.scalar(
            select(SupplierOrder).where(SupplierOrder.order_id == order_id).with_for_update()
        )
        if supplier_order is not None and supplier_order.customer_notified_at is None:
            supplier_order.customer_notified_at = datetime.now(UTC)
            await session.commit()
