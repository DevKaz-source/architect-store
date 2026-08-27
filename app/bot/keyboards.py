from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot.presentation import (
    BALANCE_BUTTON,
    CATALOG_BUTTON,
    ORDERS_BUTTON,
    SUPPORT_BUTTON,
)
from app.money import format_brl
from app.services.catalog import CatalogItem
from app.settings import Settings


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=CATALOG_BUTTON, style="primary"),
                KeyboardButton(text=BALANCE_BUTTON, style="success"),
            ],
            [
                KeyboardButton(text=ORDERS_BUTTON, style="primary"),
                KeyboardButton(text=SUPPORT_BUTTON),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Architect Store · escolha uma opção",
    )


def home_keyboard() -> InlineKeyboardMarkup:
    """Primary navigation attached to the branded storefront card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CATALOG_BUTTON,
                    callback_data="home:catalog",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="＋ Adicionar saldo via Pix",
                    callback_data="home:topup",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=BALANCE_BUTTON,
                    callback_data="home:wallet",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text=ORDERS_BUTTON,
                    callback_data="home:orders",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=SUPPORT_BUTTON,
                    callback_data="home:support",
                ),
                InlineKeyboardButton(
                    text="↻ Atualizar painel",
                    callback_data="home:refresh",
                ),
            ],
        ]
    )


def terms_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Termos", url=settings.terms_url, style="primary"
                ),
                InlineKeyboardButton(
                    text="Privacidade", url=settings.privacy_url, style="primary"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✓ Li e aceito", callback_data="terms:accept", style="success"
                )
            ],
        ]
    )


def catalog_keyboard(items: list[CatalogItem]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎁 {item.name} · {format_brl(item.price_cents)}",
                callback_data=f"product:{item.id}",
                style="primary",
            )
        ]
        for item in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
