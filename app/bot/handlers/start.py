from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.bot.keyboards import main_menu, terms_keyboard
from app.bot.presentation import WELCOME_IMAGE_PATH, environment_notice
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

    await message.answer_photo(
        FSInputFile(WELCOME_IMAGE_PATH),
        caption=(
            f"Olá, <b>{escape(user.first_name)}</b>. 👋\n\n"
            "<b>Sua loja digital está pronta.</b>\n"
            "Explore o catálogo, consulte sua carteira ou acompanhe seus pedidos."
            f"{environment_notice(settings)}"
        ),
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "terms:accept")
async def terms_accept(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None:
        return
    await accept_terms(callback.from_user.id)
    await callback.answer("Termos aceitos")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "✅ <b>Conta criada com sucesso</b>\n\n"
            "Agora escolha por onde deseja começar."
            f"{environment_notice(settings)}",
            reply_markup=main_menu(),
        )
