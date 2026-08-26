"""Integração segura com fornecedores de gift cards.

Revision ID: 0002_reloadly_supplier
Revises: 0001_initial
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_reloadly_supplier"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A revisão inicial histórica usa Base.metadata.create_all. Em um banco totalmente
    # novo ela enxerga os modelos atuais e já cria este schema; em instalações existentes
    # (revision=0001) os campos abaixo ainda não existem. O teste evita duplicação no
    # primeiro caso sem mascarar uma migração parcial.
    inspector = sa.inspect(op.get_bind())
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    order_columns = {column["name"] for column in inspector.get_columns("orders")}
    if (
        "supplier_orders" in inspector.get_table_names()
        and "fulfillment_mode" in product_columns
        and "delivery_ciphertext" in order_columns
    ):
        return

    op.add_column(
        "products",
        sa.Column(
            "fulfillment_mode",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )
    op.create_index("ix_products_fulfillment_mode", "products", ["fulfillment_mode"])

    op.alter_column(
        "orders",
        "status",
        existing_type=sa.String(length=9),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.alter_column(
        "orders",
        "stock_item_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "orders",
        "delivered_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        server_default=None,
    )
    op.add_column("orders", sa.Column("delivery_ciphertext", sa.LargeBinary(), nullable=True))
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "supplier_catalog_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("supplier", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("external_product_id", sa.String(length=80), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("denomination_type", sa.String(length=16), nullable=False),
        sa.Column("recipient_currency", sa.String(length=3), nullable=False),
        sa.Column("sender_currency", sa.String(length=3), nullable=False),
        sa.Column("product_data", sa.JSON(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supplier",
            "environment",
            "external_product_id",
            "country_code",
            name="uq_supplier_catalog_product",
        ),
    )
    op.create_index("ix_supplier_catalog_items_supplier", "supplier_catalog_items", ["supplier"])
    op.create_index(
        "ix_supplier_catalog_items_environment", "supplier_catalog_items", ["environment"]
    )
    op.create_index(
        "ix_supplier_catalog_items_external_product_id",
        "supplier_catalog_items",
        ["external_product_id"],
    )
    op.create_index(
        "ix_supplier_catalog_items_country_code", "supplier_catalog_items", ["country_code"]
    )

    op.create_table(
        "supplier_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("supplier", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("external_product_id", sa.String(length=80), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("recipient_currency", sa.String(length=3), nullable=False),
        sa.Column("sender_currency", sa.String(length=3), nullable=False),
        sa.Column("estimated_sender_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("discount_percentage", sa.Numeric(8, 4), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("unit_price > 0", name="ck_supplier_offer_unit_price_positive"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supplier",
            "environment",
            "external_product_id",
            "country_code",
            "unit_price",
            name="uq_supplier_offer_external",
        ),
    )
    op.create_index("ix_supplier_offers_product_id", "supplier_offers", ["product_id"])
    op.create_index("ix_supplier_offers_supplier", "supplier_offers", ["supplier"])
    op.create_index("ix_supplier_offers_environment", "supplier_offers", ["environment"])
    op.create_index(
        "ix_supplier_offers_external_product_id",
        "supplier_offers",
        ["external_product_id"],
    )
    op.create_index("ix_supplier_offers_active", "supplier_offers", ["active"])

    op.create_table(
        "supplier_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("supplier", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="reserved"),
        sa.Column("custom_identifier", sa.String(length=96), nullable=False),
        sa.Column("external_transaction_id", sa.String(length=96), nullable=True),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column("provider_detail", sa.String(length=500), nullable=True),
        sa.Column("provider_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("provider_currency", sa.String(length=3), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["supplier_offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("custom_identifier"),
        sa.UniqueConstraint(
            "supplier",
            "environment",
            "external_transaction_id",
            name="uq_supplier_external_transaction",
        ),
    )
    op.create_index("ix_supplier_orders_order_id", "supplier_orders", ["order_id"])
    op.create_index("ix_supplier_orders_offer_id", "supplier_orders", ["offer_id"])
    op.create_index("ix_supplier_orders_supplier", "supplier_orders", ["supplier"])
    op.create_index("ix_supplier_orders_environment", "supplier_orders", ["environment"])
    op.create_index("ix_supplier_orders_status", "supplier_orders", ["status"])


def downgrade() -> None:
    op.drop_table("supplier_orders")
    op.drop_table("supplier_offers")
    op.drop_table("supplier_catalog_items")

    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_column("orders", "delivery_ciphertext")
    op.alter_column(
        "orders",
        "delivered_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.alter_column(
        "orders",
        "stock_item_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=9),
        existing_nullable=False,
    )

    op.drop_index("ix_products_fulfillment_mode", table_name="products")
    op.drop_column("products", "fulfillment_mode")
