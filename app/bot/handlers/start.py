from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu, terms_keyboard
from app.services.users import accept_terms, get_or_create_user
from app.settings import Settings

router = Router(name="start")


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
        await message.answer(
            "Bem-vindo à <b>Architect Store</b>.\n\n"
            "Aqui você compra produtos digitais autorizados usando crédito interno. "
            "O crédito não é transferível e não permite saque.\n\n"
            "Antes de continuar, leia os termos e a política de privacidade.",
            reply_markup=terms_keyboard(settings),
        )
        return

    await message.answer(
        f"Olá, <b>{escape(user.first_name)}</b>. O que deseja fazer?",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "terms:accept")
async def terms_accept(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    await accept_terms(callback.from_user.id)
    await callback.answer("Termos aceitos")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Tudo certo. Sua conta está pronta.", reply_markup=main_menu()
        )
