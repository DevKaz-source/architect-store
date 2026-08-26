from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from app.db import SessionFactory
from app.models import (
    MessageAuthor,
    SupportMessage,
    SupportTicket,
    TicketStatus,
    User,
)
from app.security import SecretBox
from app.settings import get_settings


@dataclass(frozen=True)
class TicketResult:
    id: uuid.UUID
    public_code: str
    status: TicketStatus
    telegram_id: int
    first_name: str
    username: str | None
    latest_message: str | None = None


def _box() -> SecretBox:
    return SecretBox(get_settings().data_encryption_key)


async def create_ticket(*, telegram_id: int, message: str) -> TicketResult:
    if not message.strip():
        raise ValueError("Mensagem vazia")
    if len(message) > 3500:
        raise ValueError("Mensagem muito longa")

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            raise LookupError("Usuário não encontrado")
        open_count = await session.scalar(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.user_id == user.id,
                SupportTicket.status != TicketStatus.RESOLVED,
            )
        )
        if int(open_count or 0) >= get_settings().max_open_tickets_per_user:
            raise ValueError("Você já possui chamados em aberto; aguarde o atendimento")
        ticket = SupportTicket(
            user_id=user.id,
            subject="Atendimento solicitado pelo Telegram",
            status=TicketStatus.OPEN,
        )
        session.add(ticket)
        await session.flush()
        session.add(
            SupportMessage(
                ticket_id=ticket.id,
                author=MessageAuthor.USER,
                author_telegram_id=telegram_id,
                body_ciphertext=_box().encrypt(message.strip()),
            )
        )
        await session.commit()
        return TicketResult(
            id=ticket.id,
            public_code=ticket.public_code,
            status=ticket.status,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            username=user.username,
            latest_message=message.strip(),
        )


async def add_user_reply(*, public_code: str, telegram_id: int, message: str) -> TicketResult:
    if not message.strip() or len(message) > 3500:
        raise ValueError("Resposta vazia ou muito longa")
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(SupportTicket, User)
                .join(User, User.id == SupportTicket.user_id)
                .where(
                    SupportTicket.public_code == public_code.upper(),
                    User.telegram_id == telegram_id,
                )
                .with_for_update()
            )
        ).first()
        if row is None:
            raise LookupError("Chamado não encontrado")
        ticket, user = row
        if ticket.status == TicketStatus.RESOLVED:
            raise ValueError("Chamado encerrado; abra um novo atendimento")
        session.add(
            SupportMessage(
                ticket_id=ticket.id,
                author=MessageAuthor.USER,
                author_telegram_id=telegram_id,
                body_ciphertext=_box().encrypt(message.strip()),
            )
        )
        ticket.status = TicketStatus.OPEN
        await session.commit()
        return TicketResult(
            id=ticket.id,
            public_code=ticket.public_code,
            status=ticket.status,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            username=user.username,
            latest_message=message.strip(),
        )


async def list_open_tickets(limit: int = 20) -> list[TicketResult]:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(SupportTicket, User)
                .join(User, User.id == SupportTicket.user_id)
                .where(SupportTicket.status != TicketStatus.RESOLVED)
                .order_by(SupportTicket.updated_at)
                .limit(limit)
            )
        ).all()
        return [
            TicketResult(
                id=ticket.id,
                public_code=ticket.public_code,
                status=ticket.status,
                telegram_id=user.telegram_id,
                first_name=user.first_name,
                username=user.username,
            )
            for ticket, user in rows
        ]


async def get_ticket(public_code: str) -> tuple[TicketResult, list[tuple[str, str]]]:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(SupportTicket, User)
                .join(User, User.id == SupportTicket.user_id)
                .where(SupportTicket.public_code == public_code.upper())
            )
        ).first()
        if row is None:
            raise LookupError("Chamado não encontrado")
        ticket, user = row
        messages = (
            await session.scalars(
                select(SupportMessage)
                .where(SupportMessage.ticket_id == ticket.id)
                .order_by(SupportMessage.created_at)
            )
        ).all()
        box = _box()
        result = TicketResult(
            id=ticket.id,
            public_code=ticket.public_code,
            status=ticket.status,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            username=user.username,
        )
        return result, [(item.author.value, box.decrypt(item.body_ciphertext)) for item in messages]


async def reply_ticket(*, public_code: str, admin_telegram_id: int, message: str) -> TicketResult:
    if not message.strip() or len(message) > 3500:
        raise ValueError("Resposta vazia ou muito longa")
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(SupportTicket, User)
                .join(User, User.id == SupportTicket.user_id)
                .where(SupportTicket.public_code == public_code.upper())
                .with_for_update()
            )
        ).first()
        if row is None:
            raise LookupError("Chamado não encontrado")
        ticket, user = row
        if ticket.status == TicketStatus.RESOLVED:
            raise ValueError("Chamado já encerrado")
        session.add(
            SupportMessage(
                ticket_id=ticket.id,
                author=MessageAuthor.ADMIN,
                author_telegram_id=admin_telegram_id,
                body_ciphertext=_box().encrypt(message.strip()),
            )
        )
        ticket.status = TicketStatus.WAITING_CUSTOMER
        await session.commit()
        return TicketResult(
            id=ticket.id,
            public_code=ticket.public_code,
            status=ticket.status,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            username=user.username,
            latest_message=message.strip(),
        )


async def close_ticket(public_code: str) -> TicketResult:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(SupportTicket, User)
                .join(User, User.id == SupportTicket.user_id)
                .where(SupportTicket.public_code == public_code.upper())
                .with_for_update()
            )
        ).first()
        if row is None:
            raise LookupError("Chamado não encontrado")
        ticket, user = row
        ticket.status = TicketStatus.RESOLVED
        await session.commit()
        return TicketResult(
            id=ticket.id,
            public_code=ticket.public_code,
            status=ticket.status,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            username=user.username,
        )
