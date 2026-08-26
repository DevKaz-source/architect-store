from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionFactory
from app.models import User, Wallet


async def get_or_create_user(*, telegram_id: int, username: str | None, first_name: str) -> User:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name[:128],
            )
            session.add(user)
            try:
                await session.flush()
                session.add(Wallet(user_id=user.id, balance_cents=0))
                await session.commit()
                return user
            except IntegrityError:
                # Dois /start simultâneos: a restrição única escolhe um vencedor.
                await session.rollback()
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if user is None:
                    raise
        user.username = username
        user.first_name = first_name[:128]
        await session.commit()
        return user


async def accept_terms(telegram_id: int) -> None:
    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            raise LookupError("Usuário não encontrado")
        user.accepted_terms_at = datetime.now(UTC)
        await session.commit()


async def set_user_email(telegram_id: int, email: str) -> User:
    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            raise LookupError("Usuário não encontrado")
        user.email = email.strip().lower()
        await session.commit()
        return user


async def get_user_by_telegram(telegram_id: int) -> User | None:
    async with SessionFactory() as session:
        return await session.scalar(select(User).where(User.telegram_id == telegram_id))
