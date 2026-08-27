from __future__ import annotations

from contextlib import suppress
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove

from app.bot.keyboards import home_keyboard, terms_keyboard
from app.bot.presentation import WELCOME_IMAGE_PATH, environment_notice
from app.money import format_brl
from app.services.users import accept_terms, get_or_create_user
from app.services.wallets import get_wallet_summary
from app.settings import Settings

router = Router(name="start")


def home_caption(
    *, first_name: str, telegram_id: int, balance_cents: int, settings: Settings
) -> str:
    return (
        f"👋 Olá, <b>{escape(first_name)}</b>\n\n"
        "ℹ️ <b>Seus dados</b>\n"
        f"🆔 ID: <code>{telegram_id}</code>\n"
        f"💳 Saldo disponível: <b>{format_brl(balance_cents)}</b>\n"
        "⚡ Entrega digital automatizada\n\n"
        "<b>O que você deseja fazer?</b>"
        f"{environment_notice(settings)}"
    )


async def send_home(
    message: Message, *, telegram_id: int, first_name: str, settings: Settings
) -> None:
    summary = await get_wallet_summary(telegram_id)
    reset_message = await message.answer(
        "Abrindo a Architect Store…",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer_photo(
        FSInputFile(WELCOME_IMAGE_PATH),
        caption=home_caption(
            first_name=first_name,
            telegram_id=telegram_id,
            balance_cents=summary.balance_cents,
            settings=settings,
        ),
        reply_markup=home_keyboard(),
        show_caption_above_media=True,
    )
    with suppress(TelegramBadRequest):
        await reset_message.delete()


@router.message(CommandStart())
async def start(message: Message, settings: Settings) -> None:
    if message.from_user is None:
        return
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )
    if user.accepted_terms_at is None:
        await message.answer_photo(
            FSInputFile(WELCOME_IMAGE_PATH),
            caption=(
                "<b>Bem-vindo à Architect Store</b>\n\n"
                "Produtos digitais, pagamento via Pix e entrega automatizada em uma "
                "experiência segura dentro do Telegram.\n\n"
                "O crédito interno não é transferível e não permite saque. Antes de "
                "continuar, leia os termos e a política de privacidade."
                f"{environment_notice(settings)}"
            ),
            reply_markup=terms_keyboard(settings),
        )
        return

    await send_home(
        message,
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        settings=settings,
    )


@router.callback_query(F.data == "terms:accept")
async def terms_accept(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None:
        return
    await accept_terms(callback.from_user.id)
    await callback.answer("Termos aceitos")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await send_home(
            callback.message,
            telegram_id=callback.from_user.id,
            first_name=callback.from_user.first_name,
            settings=settings,
        )


@router.callback_query(F.data == "home:refresh")
async def home_refresh(callback: CallbackQuery, settings: Settings) -> None:
    summary = await get_wallet_summary(callback.from_user.id)
    if callback.message:
        try:
            await callback.message.edit_caption(
                caption=home_caption(
                    first_name=callback.from_user.first_name,
                    telegram_id=callback.from_user.id,
                    balance_cents=summary.balance_cents,
                    settings=settings,
                ),
                reply_markup=home_keyboard(),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
            await callback.answer("O painel já está atualizado")
            return
    await callback.answer("Painel atualizado")
