from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.types import BotCommand, FSInputFile, InputProfilePhotoStatic

from app.settings import Settings

STORE_NAME = "Architect Store"
ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
AVATAR_PATH = ASSET_DIR / "architect-store-avatar.jpg"
WELCOME_IMAGE_PATH = ASSET_DIR / "architect-store-welcome.jpg"

CATALOG_BUTTON = "🛍️ Explorar catálogo"
BALANCE_BUTTON = "💳 Minha carteira"
ORDERS_BUTTON = "📦 Meus pedidos"
SUPPORT_BUTTON = "💬 Falar com suporte"

LEGACY_CATALOG_BUTTON = "🛍 Catálogo"
LEGACY_BALANCE_BUTTON = "💰 Meu saldo"
LEGACY_ORDERS_BUTTON = "📦 Minhas compras"
LEGACY_SUPPORT_BUTTON = "🆘 Suporte"

BOT_COMMANDS = [
    BotCommand(command="start", description="Abrir a Architect Store"),
    BotCommand(command="catalogo", description="Explorar o catálogo"),
    BotCommand(command="saldo", description="Consultar minha carteira"),
    BotCommand(command="compras", description="Acompanhar meus pedidos"),
    BotCommand(command="suporte", description="Falar com o suporte"),
    BotCommand(command="responder_suporte", description="Responder a um chamado"),
    BotCommand(command="cancelar", description="Cancelar a operação atual"),
]


def is_demo_environment(settings: Settings) -> bool:
    return (
        settings.app_env != "production"
        or settings.pix_provider == "mock"
        or settings.active_giftcard_provider == "mock"
    )


def environment_notice(settings: Settings) -> str:
    if not is_demo_environment(settings):
        return ""
    return (
        "\n\n🧪 <b>Ambiente de demonstração</b>\n"
        "Pagamentos e produtos fictícios; nenhum valor real é movimentado."
    )


async def configure_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)


async def apply_telegram_brand(bot: Bot, settings: Settings) -> None:
    demo = is_demo_environment(settings)
    short_description = (
        "Demonstração de comércio digital com Pix, entrega automática e suporte integrado."
        if demo
        else "Produtos digitais com Pix, entrega automática segura e suporte integrado."
    )
    description = (
        "Demonstração da Architect Store: catálogo, carteira, Pix sandbox, compra "
        "idempotente, entrega protegida e suporte. Produtos e pagamentos são "
        "fictícios neste ambiente."
        if demo
        else "Compre produtos digitais pela Architect Store com Pix, entrega "
        "automática protegida, histórico de pedidos e suporte integrado."
    )

    await bot.set_my_name(name=STORE_NAME)
    await bot.set_my_short_description(short_description=short_description)
    await bot.set_my_description(description=description)
    await configure_bot_commands(bot)
    await bot.set_my_profile_photo(
        photo=InputProfilePhotoStatic(photo=FSInputFile(AVATAR_PATH))
    )
