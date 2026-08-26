from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.suppliers.base import (
    GiftCardProduct,
    SupplierAmbiguousError,
    SupplierBalance,
    SupplierRedeemCode,
    SupplierRejectedError,
    SupplierTransaction,
    SupplierUnavailableError,
)

MockScenario = Literal[
    "success", "pending_then_success", "ambiguous_then_success", "reject"
]


@dataclass(frozen=True)
class MockSeedProduct:
    slug: str
    name: str
    description: str
    sale_price_cents: int
    external_product_id: str
    denomination: Decimal


MOCK_SEED_PRODUCTS = (
    MockSeedProduct(
        slug="teste-mock-gift-card-10",
        name="[TESTE] Gift Card fictício R$ 10",
        description=(
            "SIMULAÇÃO sem valor real. Testa compra, débito e entrega automática de código."
        ),
        sale_price_cents=1_290,
        external_product_id="900001",
        denomination=Decimal("10"),
    ),
    MockSeedProduct(
        slug="teste-mock-streaming-30d",
        name="[TESTE] Streaming fictício · 30 dias",
        description=(
            "SIMULAÇÃO sem acesso real. Testa a venda automática de uma assinatura digital."
        ),
        sale_price_cents=2_490,
        external_product_id="900002",
        denomination=Decimal("20"),
    ),
    MockSeedProduct(
        slug="teste-mock-criativo-30d",
        name="[TESTE] Software criativo fictício · 30 dias",
        description=(
            "SIMULAÇÃO sem licença real. Testa compra e entrega automática de software."
        ),
        sale_price_cents=3_990,
        external_product_id="900003",
        denomination=Decimal("35"),
    ),
)


def _product(
    product_id: str,
    name: str,
    denominations_and_costs: tuple[tuple[str, str], ...],
) -> GiftCardProduct:
    price_map = {
        Decimal(denomination): Decimal(cost)
        for denomination, cost in denominations_and_costs
    }
    denominations = tuple(price_map)
    return GiftCardProduct(
        product_id=product_id,
        name=name,
        country_code="BR",
        status="ACTIVE",
        denomination_type="FIXED",
        recipient_currency="BRL",
        sender_currency="BRL",
        fixed_recipient_denominations=denominations,
        recipient_to_sender=price_map,
        min_recipient_denomination=None,
        max_recipient_denomination=None,
        discount_percentage=None,
        user_id_required=False,
        raw={
            "fixedRecipientDenominations": [str(value) for value in denominations],
            "senderFee": "0",
            "senderFeePercentage": "0",
            "testOnly": True,
        },
    )


MOCK_PRODUCTS = (
    _product(
        "900001",
        "[TESTE] Gift Card fictício",
        (("10", "8.50"), ("25", "21.25"), ("50", "42.50")),
    ),
    _product(
        "900002",
        "[TESTE] Streaming fictício · 30 dias",
        (("20", "16"),),
    ),
    _product(
        "900003",
        "[TESTE] Software criativo fictício · 30 dias",
        (("35", "28"),),
    ),
)


class MockGiftCardSupplier:
    """Fornecedor determinístico, local e sem produtos ou códigos com valor real."""

    name = "mock"
    environment = "sandbox"

    def __init__(self, *, scenario: MockScenario = "success") -> None:
        self.scenario = scenario
        self._transactions_by_custom: dict[str, SupplierTransaction] = {}

    async def get_balance(self) -> SupplierBalance:
        return SupplierBalance(amount=Decimal("10000"), currency="BRL")

    async def list_products(self, country_code: str) -> list[GiftCardProduct]:
        code = country_code.strip().upper()
        if len(code) != 2 or not code.isalpha():
            raise ValueError("Código de país precisa ter duas letras, como BR")
        return list(MOCK_PRODUCTS) if code == "BR" else []

    async def get_product(self, product_id: str) -> GiftCardProduct:
        normalized = product_id.strip()
        product = next(
            (item for item in MOCK_PRODUCTS if item.product_id == normalized),
            None,
        )
        if product is None:
            raise SupplierUnavailableError(f"Produto mock {normalized!r} não existe")
        return product

    @staticmethod
    def _transaction_id(
        *, product_id: str, unit_price: Decimal, custom_identifier: str
    ) -> str:
        return f"MOCK:{product_id}:{unit_price}:{custom_identifier}"

    @staticmethod
    def _parse_transaction_id(transaction_id: str) -> tuple[str, Decimal, str]:
        parts = transaction_id.split(":", 3)
        if len(parts) != 4 or parts[0] != "MOCK":
            raise SupplierUnavailableError("Transação mock inválida")
        try:
            unit_price = Decimal(parts[2])
        except ArithmeticError as exc:
            raise SupplierUnavailableError("Transação mock inválida") from exc
        return parts[1], unit_price, parts[3]

    async def _transaction(
        self,
        *,
        product_id: str,
        unit_price: Decimal,
        custom_identifier: str,
        status: str,
    ) -> SupplierTransaction:
        product = await self.get_product(product_id)
        if unit_price not in product.fixed_recipient_denominations:
            raise SupplierRejectedError("Denominação indisponível no fornecedor mock")
        cost = product.estimated_sender_cost(unit_price)
        if cost is None:
            raise SupplierUnavailableError("Custo mock indisponível")
        return SupplierTransaction(
            transaction_id=self._transaction_id(
                product_id=product_id,
                unit_price=unit_price,
                custom_identifier=custom_identifier,
            ),
            custom_identifier=custom_identifier,
            status=status,
            amount=cost,
            currency="BRL",
            product_id=product_id,
            unit_price=unit_price,
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
        if not sender_name.strip():
            raise SupplierRejectedError("Nome do remetente não foi informado")
        if self.scenario == "reject":
            raise SupplierRejectedError(
                "Cenário mock: pedido rejeitado sem cobrança externa"
            )
        existing = self._transactions_by_custom.get(custom_identifier)
        if existing is not None:
            return existing
        status = "PENDING" if self.scenario == "pending_then_success" else "SUCCESSFUL"
        transaction = await self._transaction(
            product_id=product_id,
            unit_price=unit_price,
            custom_identifier=custom_identifier,
            status=status,
        )
        self._transactions_by_custom[custom_identifier] = transaction
        if self.scenario == "ambiguous_then_success":
            raise SupplierAmbiguousError(
                "Cenário mock: resposta perdida após criar a transação"
            )
        return transaction

    async def get_transaction(self, transaction_id: str) -> SupplierTransaction:
        product_id, unit_price, custom_identifier = self._parse_transaction_id(
            transaction_id
        )
        transaction = await self._transaction(
            product_id=product_id,
            unit_price=unit_price,
            custom_identifier=custom_identifier,
            status="SUCCESSFUL",
        )
        self._transactions_by_custom[custom_identifier] = transaction
        return transaction

    async def find_transaction(self, custom_identifier: str) -> SupplierTransaction | None:
        return self._transactions_by_custom.get(custom_identifier)

    async def get_redeem_code(self, transaction_id: str) -> SupplierRedeemCode:
        self._parse_transaction_id(transaction_id)
        digest = hashlib.sha256(transaction_id.encode()).hexdigest().upper()
        return SupplierRedeemCode(
            card_number=f"TESTE-SEM-VALOR-{digest[:12]}",
            pin_code="0000",
            redemption_url=None,
        )

    async def aclose(self) -> None:
        return None
