from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class InvalidMoney(ValueError):
    pass


def parse_brl_to_cents(raw: str) -> int:
    value = raw.strip().upper().replace("R$", "").replace(" ", "")
    if not value:
        raise InvalidMoney("Informe um valor")

    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") > 1:
        parts = value.split(".")
        value = "".join(parts[:-1]) + "." + parts[-1]

    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidMoney("Valor inválido") from exc

    if not amount.is_finite() or amount <= 0:
        raise InvalidMoney("O valor precisa ser maior que zero")
    if amount.as_tuple().exponent < -2:
        raise InvalidMoney("Use no máximo duas casas decimais")

    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_brl(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    reais, centavos = divmod(absolute, 100)
    grouped = f"{reais:,}".replace(",", ".")
    return f"{sign}R$ {grouped},{centavos:02d}"


def cents_to_api_amount(cents: int) -> str:
    if cents <= 0:
        raise InvalidMoney("O valor precisa ser maior que zero")
    return f"{cents // 100}.{cents % 100:02d}"
