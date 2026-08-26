from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.services.catalog import CatalogItem
from app.settings import Settings


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Catálogo"), KeyboardButton(text="💰 Meu saldo")],
            [KeyboardButton(text="📦 Minhas compras"), KeyboardButton(text="🆘 Suporte")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Escolha uma opção",
    )


def terms_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Termos", url=settings.terms_url),
                InlineKeyboardButton(text="Privacidade", url=settings.privacy_url),
            ],
            [InlineKeyboardButton(text="✅ Li e aceito", callback_data="terms:accept")],
        ]
    )


def catalog_keyboard(items: list[CatalogItem]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{item.name} · {item.availability_label}",
                callback_data=f"product:{item.id}",
            )
        ]
        for item in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
