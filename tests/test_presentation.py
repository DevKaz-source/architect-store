from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.bot.keyboards import catalog_keyboard, main_menu
from app.bot.presentation import (
    AVATAR_PATH,
    BALANCE_BUTTON,
    CATALOG_BUTTON,
    ORDERS_BUTTON,
    STORE_NAME,
    SUPPORT_BUTTON,
    WELCOME_IMAGE_PATH,
    apply_telegram_brand,
    environment_notice,
)
from app.services.catalog import CatalogItem
from app.settings import Settings


def test_brand_assets_are_bundled() -> None:
    assert AVATAR_PATH.is_file()
    assert AVATAR_PATH.suffix == ".jpg"
    assert AVATAR_PATH.stat().st_size > 50_000
    assert WELCOME_IMAGE_PATH.is_file()
    assert WELCOME_IMAGE_PATH.stat().st_size > 50_000


def test_main_menu_uses_brand_labels_and_button_styles() -> None:
    menu = main_menu()
    assert [[button.text for button in row] for row in menu.keyboard] == [
        [CATALOG_BUTTON, BALANCE_BUTTON],
        [ORDERS_BUTTON, SUPPORT_BUTTON],
    ]
    assert [[button.style for button in row] for row in menu.keyboard] == [
        ["primary", "success"],
        ["primary", None],
    ]
    assert menu.is_persistent is True


def test_catalog_keyboard_shows_product_and_price() -> None:
    item = CatalogItem(
        id=7,
        name="Produto Demo",
        description="Somente teste",
        price_cents=1290,
        stock_count=None,
    )
    keyboard = catalog_keyboard([item])
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "🎁 Produto Demo · R$ 12,90"
    assert button.callback_data == "product:7"
    assert button.style == "primary"


def test_demo_environment_is_explicit() -> None:
    notice = environment_notice(Settings(app_env="development", pix_provider="mock"))
    assert "Ambiente de demonstração" in notice
    assert "nenhum valor real" in notice


@pytest.mark.asyncio
async def test_apply_telegram_brand_updates_public_profile() -> None:
    bot = AsyncMock()
    settings = Settings(app_env="development", pix_provider="mock")

    await apply_telegram_brand(bot, settings)

    bot.set_my_name.assert_awaited_once_with(name=STORE_NAME)
    bot.set_my_short_description.assert_awaited_once()
    bot.set_my_description.assert_awaited_once()
    bot.set_my_commands.assert_awaited_once()
    bot.set_my_profile_photo.assert_awaited_once()
