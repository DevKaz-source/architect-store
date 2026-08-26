from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.db import SessionFactory
from app.models import User, Wallet, WalletEntry


@dataclass(frozen=True)
class WalletLine:
    amount_cents: int
    balance_after_cents: int
    note: str | None
    created_at: datetime


@dataclass(frozen=True)
class WalletSummary:
    balance_cents: int
    entries: list[WalletLine]


async def get_wallet_summary(telegram_id: int, limit: int = 5) -> WalletSummary:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(User, Wallet)
                .join(Wallet, Wallet.user_id == User.id)
                .where(User.telegram_id == telegram_id)
            )
        ).first()
        if row is None:
            raise LookupError("Carteira não encontrada")
        user, wallet = row
        entries = (
            await session.scalars(
                select(WalletEntry)
                .where(WalletEntry.user_id == user.id)
                .order_by(WalletEntry.created_at.desc())
                .limit(limit)
            )
        ).all()
        return WalletSummary(
            balance_cents=wallet.balance_cents,
            entries=[
                WalletLine(
                    amount_cents=item.amount_cents,
                    balance_after_cents=item.balance_after_cents,
                    note=item.note,
                    created_at=item.created_at,
                )
                for item in entries
            ],
        )
