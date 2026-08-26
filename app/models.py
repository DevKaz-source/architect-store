from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db import Base


def _enum(enum_type: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def _public_code(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


class DeliveryType(StrEnum):
    CODE = "code"
    CREDENTIALS = "credentials"
    LINK = "link"
    INSTRUCTIONS = "instructions"


class StockStatus(StrEnum):
    AVAILABLE = "available"
    SOLD = "sold"
    DISABLED = "disabled"


class DepositStatus(StrEnum):
    CREATING = "creating"
    PENDING = "pending"
    CREDITED = "credited"
    EXPIRED = "expired"
    CANCELED = "canceled"
    FAILED = "failed"
    REFUNDED = "refunded"
    CHARGED_BACK = "charged_back"
    REVIEW = "review"


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    DELIVERED = "delivered"
    REFUNDED = "refunded"
    REVIEW = "review"
    DISPUTED = "disputed"


class LedgerEntryType(StrEnum):
    DEPOSIT = "deposit"
    PURCHASE = "purchase"
    REFUND = "refund"
    REVERSAL = "reversal"
    ADJUSTMENT = "adjustment"


class TicketStatus(StrEnum):
    OPEN = "open"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"


class MessageAuthor(StrEnum):
    USER = "user"
    ADMIN = "admin"


class FulfillmentMode(StrEnum):
    MANUAL = "manual"
    SUPPLIER = "supplier"


class SupplierOrderStatus(StrEnum):
    RESERVED = "reserved"
    SUBMITTED = "submitted"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    REVIEW = "review"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(320))
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Wallet(Base):
    __tablename__ = "wallets"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance_cents: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WalletEntry(Base):
    __tablename__ = "wallet_entries"
    __table_args__ = (CheckConstraint("amount_cents <> 0", name="ck_wallet_entry_nonzero"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    entry_type: Mapped[LedgerEntryType] = mapped_column(_enum(LedgerEntryType, "ledger_entry_type"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    balance_after_cents: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[str] = mapped_column(String(64), index=True)
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("price_cents > 0", name="ck_product_price_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(BigInteger)
    delivery_type: Mapped[DeliveryType] = mapped_column(_enum(DeliveryType, "delivery_type"))
    fulfillment_mode: Mapped[FulfillmentMode] = mapped_column(
        _enum(FulfillmentMode, "fulfillment_mode"),
        default=FulfillmentMode.MANUAL,
        server_default=FulfillmentMode.MANUAL.value,
        index=True,
    )
    supplier_reference: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupplierCatalogItem(Base):
    __tablename__ = "supplier_catalog_items"
    __table_args__ = (
        UniqueConstraint(
            "supplier",
            "environment",
            "external_product_id",
            "country_code",
            name="uq_supplier_catalog_product",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier: Mapped[str] = mapped_column(String(32), index=True)
    environment: Mapped[str] = mapped_column(String(16), index=True)
    external_product_id: Mapped[str] = mapped_column(String(80), index=True)
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    name: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(32))
    denomination_type: Mapped[str] = mapped_column(String(16))
    recipient_currency: Mapped[str] = mapped_column(String(3))
    sender_currency: Mapped[str] = mapped_column(String(3))
    product_data: Mapped[dict] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupplierOffer(Base):
    __tablename__ = "supplier_offers"
    __table_args__ = (
        UniqueConstraint(
            "supplier",
            "environment",
            "external_product_id",
            "country_code",
            "unit_price",
            name="uq_supplier_offer_external",
        ),
        CheckConstraint("unit_price > 0", name="ck_supplier_offer_unit_price_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    supplier: Mapped[str] = mapped_column(String(32), index=True)
    environment: Mapped[str] = mapped_column(String(16), index=True)
    external_product_id: Mapped[str] = mapped_column(String(80), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    recipient_currency: Mapped[str] = mapped_column(String(3))
    sender_currency: Mapped[str] = mapped_column(String(3))
    estimated_sender_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    discount_percentage: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StockItem(Base):
    __tablename__ = "stock_items"
    __table_args__ = (
        Index("ix_stock_available", "product_id", "status"),
        UniqueConstraint("product_id", "payload_fingerprint", name="uq_stock_product_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    payload_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[StockStatus] = mapped_column(
        _enum(StockStatus, "stock_status"),
        default=StockStatus.AVAILABLE,
        server_default=StockStatus.AVAILABLE.value,
    )
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Deposit(Base):
    __tablename__ = "deposits"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_deposit_amount_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[DepositStatus] = mapped_column(
        _enum(DepositStatus, "deposit_status"),
        default=DepositStatus.CREATING,
        server_default=DepositStatus.CREATING.value,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32))
    provider_order_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(80))
    external_reference: Mapped[str] = mapped_column(String(80), unique=True)
    ticket_url: Mapped[str | None] = mapped_column(Text)
    qr_code: Mapped[str | None] = mapped_column(Text)
    qr_code_base64: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(String(40))
    provider_status_detail: Mapped[str | None] = mapped_column(String(80))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (CheckConstraint("unit_price_cents > 0", name="ck_order_price_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_code: Mapped[str] = mapped_column(
        String(24), unique=True, default=lambda: _public_code("PED")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stock_items.id", ondelete="RESTRICT"), unique=True
    )
    delivery_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"),
        default=OrderStatus.DELIVERED,
        server_default=OrderStatus.DELIVERED.value,
        index=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupplierOrder(Base):
    __tablename__ = "supplier_orders"
    __table_args__ = (
        UniqueConstraint(
            "supplier",
            "environment",
            "external_transaction_id",
            name="uq_supplier_external_transaction",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), unique=True, index=True
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_offers.id", ondelete="RESTRICT"), index=True
    )
    supplier: Mapped[str] = mapped_column(String(32), index=True)
    environment: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[SupplierOrderStatus] = mapped_column(
        _enum(SupplierOrderStatus, "supplier_order_status"),
        default=SupplierOrderStatus.RESERVED,
        server_default=SupplierOrderStatus.RESERVED.value,
        index=True,
    )
    custom_identifier: Mapped[str] = mapped_column(String(96), unique=True)
    external_transaction_id: Mapped[str | None] = mapped_column(String(96))
    provider_status: Mapped[str | None] = mapped_column(String(32))
    provider_detail: Mapped[str | None] = mapped_column(String(500))
    provider_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    provider_currency: Mapped[str | None] = mapped_column(String(3))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_code: Mapped[str] = mapped_column(
        String(24), unique=True, default=lambda: _public_code("SUP")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    subject: Mapped[str] = mapped_column(String(160))
    status: Mapped[TicketStatus] = mapped_column(
        _enum(TicketStatus, "ticket_status"),
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True
    )
    author: Mapped[MessageAuthor] = mapped_column(_enum(MessageAuthor, "message_author"))
    author_telegram_id: Mapped[int] = mapped_column(BigInteger)
    body_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(96))
    topic: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(96), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
