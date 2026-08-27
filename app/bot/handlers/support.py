from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.presentation import LEGACY_SUPPORT_BUTTON, SUPPORT_BUTTON
from app.services.support import TicketResult, add_user_reply, create_ticket
from app.settings import Settings

router = Router(name="support")


class SupportStates(StatesGroup):
    waiting_message = State()


async def _notify_staff(
    *, bot: Bot, settings: Settings, ticket: TicketResult, heading: str
) -> None:
    username = f"@{ticket.username}" if ticket.username else "sem username"
    notification = (
        f"🆘 <b>{escape(heading)} · {ticket.public_code}</b>\n"
        f"Cliente: {escape(ticket.first_name)} · {escape(username)} · "
        f"<code>{ticket.telegram_id}</code>\n"
        f"Responder: <code>/responder {ticket.public_code} sua mensagem</code>"
    )
    destinations = (
        {settings.support_chat_id} if settings.support_chat_id else set(settings.admin_ids)
    )
    for destination in destinations:
        try:
            await bot.send_message(destination, notification)
            await bot.send_message(
                destination,
                ticket.latest_message or "",
                parse_mode=None,
                protect_content=True,
            )
        except Exception:
            continue


@router.message(Command("suporte"))
@router.message(F.text.in_({SUPPORT_BUTTON, LEGACY_SUPPORT_BUTTON}))
async def support_start(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportStates.waiting_message)
    await message.answer(
        "💬 <b>Central de suporte</b>\n\n"
        "Descreva o problema em uma única mensagem. Inclua o código do pedido, se houver.\n\n"
        "Não envie senhas pessoais, documentos ou dados bancários. Use /cancelar para sair."
    )


@router.callback_query(F.data == "home:support")
async def support_from_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupportStates.waiting_message)
    if callback.message:
        await callback.message.answer(
            "💬 <b>Central de suporte</b>\n\n"
            "Descreva o problema em uma única mensagem. Inclua o código do pedido, "
            "se houver.\n\n"
            "Não envie senhas pessoais, documentos ou dados bancários. "
            "Use /cancelar para sair."
        )
    await callback.answer()


@router.message(Command("responder_suporte"))
async def user_reply(message: Message, settings: Settings, bot: Bot) -> None:
    if message.from_user is None or not message.text:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Uso: <code>/responder_suporte SUP-XXXXXXXX sua mensagem</code>")
        return
    try:
        ticket = await add_user_reply(
            public_code=parts[1],
            telegram_id=message.from_user.id,
            message=parts[2],
        )
    except (LookupError, ValueError) as exc:
        await message.answer(escape(str(exc)))
        return
    await message.answer(f"Resposta adicionada ao chamado <code>{ticket.public_code}</code>.")
    await _notify_staff(
        bot=bot,
        settings=settings,
        ticket=ticket,
        heading="Cliente respondeu",
    )


@router.message(SupportStates.waiting_message)
async def support_receive(
    message: Message, state: FSMContext, settings: Settings, bot: Bot
) -> None:
    if message.from_user is None or not message.text:
        await message.answer("Envie o relato em texto.")
        return
    try:
        ticket = await create_ticket(telegram_id=message.from_user.id, message=message.text)
    except ValueError as exc:
        await message.answer(escape(str(exc)))
        return
    await state.clear()
    await message.answer(
        f"✅ Chamado <code>{ticket.public_code}</code> aberto. A equipe responderá por este bot."
    )

    await _notify_staff(
        bot=bot,
        settings=settings,
        ticket=ticket,
        heading="Novo chamado",
    )
