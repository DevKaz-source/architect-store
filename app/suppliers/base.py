from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

BASIS_POINTS = Decimal(10_000)


class SupplierError(RuntimeError):
    """Falha segura para exibição/log sem incluir credenciais."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class SupplierConfigurationError(SupplierError):
    pass


class SupplierRejectedError(SupplierError):
    """A API rejeitou o pedido antes de criar uma transação."""


class SupplierAmbiguousError(SupplierError):
    """Não é seguro repetir nem reembolsar sem conciliar no fornecedor."""


class SupplierUnavailableError(SupplierError):
    pass


def minimum_sale_for_gross_margin(cost: Decimal, margin_bps: int) -> Decimal:
    """Return the minimum sale price for (sale - cost) / sale >= margin."""
    margin = Decimal(margin_bps) / BASIS_POINTS
    return cost / (Decimal(1) - margin)


@dataclass(frozen=True)
class GiftCardProduct:
    product_id: str
    name: str
    country_code: str
    status: str
    denomination_type: str
    recipient_currency: str
    sender_currency: str
    fixed_recipient_denominations: tuple[Decimal, ...]
    recipient_to_sender: dict[Decimal, Decimal]
    min_recipient_denomination: Decimal | None
    max_recipient_denomination: Decimal | None
    discount_percentage: Decimal | None
    user_id_required: bool
    raw: dict

    def estimated_sender_cost(self, denomination: Decimal) -> Decimal | None:
        """Estimativa conservadora: conversão + taxas, sem descontar promoções."""
        base_cost = self.recipient_to_sender.get(denomination)
        if base_cost is None:
            return None
        try:
            flat_fee = Decimal(str(self.raw.get("senderFee") or 0))
            percentage = Decimal(str(self.raw.get("senderFeePercentage") or 0))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not flat_fee.is_finite() or not percentage.is_finite():
            return None
        return base_cost + flat_fee + (base_cost * percentage / Decimal(100))


@dataclass(frozen=True)
class SupplierBalance:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class SupplierTransaction:
    transaction_id: str
    custom_identifier: str
    status: str
    amount: Decimal | None
    currency: str | None
    product_id: str | None
    unit_price: Decimal | None
    quantity: int | None


@dataclass(frozen=True)
class SupplierRedeemCode:
    card_number: str | None
    pin_code: str | None
    redemption_url: str | None

    def as_delivery(self) -> str:
        rows: list[str] = []
        if self.card_number:
            rows.append(f"Código: {self.card_number}")
        if self.pin_code:
            rows.append(f"PIN: {self.pin_code}")
        if self.redemption_url:
            rows.append(f"Link de ativação: {self.redemption_url}")
        if not rows:
            raise SupplierError("O fornecedor não retornou código, PIN nem link de ativação")
        return "\n".join(rows)


class GiftCardSupplier(Protocol):
    name: str
    environment: str

    async def get_balance(self) -> SupplierBalance: ...

    async def list_products(self, country_code: str) -> list[GiftCardProduct]: ...

    async def get_product(self, product_id: str) -> GiftCardProduct: ...

    async def order(
        self,
        *,
        product_id: str,
        unit_price: Decimal,
        custom_identifier: str,
        sender_name: str,
    ) -> SupplierTransaction: ...

    async def get_transaction(self, transaction_id: str) -> SupplierTransaction: ...

    async def find_transaction(self, custom_identifier: str) -> SupplierTransaction | None: ...

    async def get_redeem_code(self, transaction_id: str) -> SupplierRedeemCode: ...

    async def aclose(self) -> None: ...
