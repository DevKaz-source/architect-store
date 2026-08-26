from __future__ import annotations

from html import escape

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services.support import close_ticket, get_ticket, list_open_tickets, reply_ticket
from app.settings import Settings

router = Router(name="admin")


def _authorized(message: Message, settings: Settings) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_ids)


@router.message(Command("admin"))
async def admin_help(message: Message, settings: Settings) -> None:
    if not _authorized(message, settings):
        return
    await message.answer(
        "<b>Administração</b>\n"
        "/tickets — chamados abertos\n"
        "/ver SUP-XXXXXXXX — histórico do chamado\n"
        "/responder SUP-XXXXXXXX mensagem — responder\n"
        "/fechar SUP-XXXXXXXX — encerrar\n\n"
        "Produtos e estoque são administrados pelo CLI do servidor para não expor "
        "credenciais no Telegram."
    )


@router.message(Command("tickets"))
async def tickets(message: Message, settings: Settings) -> None:
    if not _authorized(message, settings):
        return
    items = await list_open_tickets()
    if not items:
        await message.answer("Nenhum chamado aberto.")
        return
    lines = ["<b>Chamados abertos</b>"]
    for item in items:
        username = f"@{item.username}" if item.username else "sem username"
        lines.append(
            f"• <code>{item.public_code}</code> · {escape(item.first_name)} · "
            f"{escape(username)} · {item.status.value}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("ver"))
async def view_ticket(message: Message, settings: Settings) -> None:
    if not _authorized(message, settings) or not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Uso: <code>/ver SUP-XXXXXXXX</code>")
        return
    try:
        ticket, history = await get_ticket(parts[1])
    except LookupError as exc:
        await message.answer(escape(str(exc)))
        return
    await message.answer(f"<b>{ticket.public_code}</b> · {ticket.status.value}")
    for author, body in history:
        label = "Cliente" if author == "user" else "Atendente"
        await message.answer(
            f"{label}\n{body}",
            parse_mode=None,
            protect_content=True,
        )


@router.message(Command("responder"))
async def reply(message: Message, settings: Settings, bot: Bot) -> None:
    if not _authorized(message, settings) or not message.text or not message.from_user:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Uso: <code>/responder SUP-XXXXXXXX sua mensagem</code>")
        return
    try:
        ticket = await reply_ticket(
            public_code=parts[1],
            admin_telegram_id=message.from_user.id,
            message=parts[2],
        )
    except (LookupError, ValueError) as exc:
        await message.answer(escape(str(exc)))
        return
    await bot.send_message(
        ticket.telegram_id,
        f"💬 <b>Resposta do suporte · {ticket.public_code}</b>",
    )
    await bot.send_message(
        ticket.telegram_id,
        ticket.latest_message or "",
        parse_mode=None,
        protect_content=True,
    )
    await bot.send_message(
        ticket.telegram_id,
        f"Para responder neste chamado, use:\n"
        f"<code>/responder_suporte {ticket.public_code} sua mensagem</code>",
    )
    await message.answer("Resposta enviada.")


@router.message(Command("fechar"))
async def close(message: Message, settings: Settings, bot: Bot) -> None:
    if not _authorized(message, settings) or not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Uso: <code>/fechar SUP-XXXXXXXX</code>")
        return
    try:
        ticket = await close_ticket(parts[1])
    except LookupError as exc:
        await message.answer(escape(str(exc)))
        return
    await bot.send_message(
        ticket.telegram_id,
        f"✅ O chamado <code>{ticket.public_code}</code> foi encerrado. "
        "Se precisar, abra um novo em 🆘 Suporte.",
    )
    await message.answer("Chamado encerrado.")
