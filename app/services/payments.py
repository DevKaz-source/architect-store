from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.db import SessionFactory
from app.models import (
    Deposit,
    DepositStatus,
    LedgerEntryType,
    User,
    Wallet,
    WalletEntry,
    WebhookEvent,
)
from app.payments.base import PixOrderSnapshot, PixProvider, PixProviderRejected
from app.settings import Settings


class DepositMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class DepositPayment:
    id: uuid.UUID
    amount_cents: int
    status: DepositStatus
    provider_order_id: str
    ticket_url: str | None
    qr_code: str | None
    qr_code_base64: str | None
    expires_at: datetime


@dataclass(frozen=True)
class ReconcileResult:
    changed: bool
    event: str
    telegram_id: int
    amount_cents: int
    balance_cents: int | None
    deposit_id: uuid.UUID


@dataclass(frozen=True)
class DepositLookup:
    id: uuid.UUID
    provider_order_id: str
    status: DepositStatus
    amount_cents: int


async def create_deposit(
    *,
    user_id: int,
    payer_email: str,
    amount_cents: int,
    provider: PixProvider,
    settings: Settings,
) -> DepositPayment:
    if not settings.min_topup_cents <= amount_cents <= settings.max_topup_cents:
        raise ValueError("Valor fora dos limites permitidos")

    deposit_id = uuid.uuid4()
    external_reference = f"credit_{deposit_id.hex}"
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.pix_expiration_minutes)

    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise LookupError("Usuário não encontrado")
        if user.is_blocked:
            raise ValueError("Sua conta está bloqueada para operações financeiras")
        now = datetime.now(UTC)
        recent_creation = now - timedelta(minutes=10)
        open_deposits = await session.scalar(
            select(func.count(Deposit.id)).where(
                Deposit.user_id == user_id,
                or_(
                    Deposit.status == DepositStatus.REVIEW,
                    and_(
                        Deposit.status == DepositStatus.PENDING,
                        Deposit.expires_at > now,
                    ),
                    and_(
                        Deposit.status == DepositStatus.CREATING,
                        Deposit.created_at > recent_creation,
                    ),
                ),
            )
        )
        if int(open_deposits or 0) >= settings.max_open_deposits_per_user:
            raise ValueError(
                "Você já possui pagamentos Pix em aberto; conclua-os ou aguarde a expiração"
            )
        session.add(
            Deposit(
                id=deposit_id,
                user_id=user_id,
                amount_cents=amount_cents,
                status=DepositStatus.CREATING,
                provider=provider.name,
                external_reference=external_reference,
                expires_at=expires_at,
            )
        )
        await session.commit()

    try:
        snapshot = await provider.create_pix(
            amount_cents=amount_cents,
            external_reference=external_reference,
            payer_email=payer_email,
            idempotency_key=str(deposit_id),
            expiration_minutes=settings.pix_expiration_minutes,
        )
    except Exception as exc:
        async with SessionFactory() as session:
            deposit = await session.get(Deposit, deposit_id)
            if deposit is not None:
                deposit.status = (
                    DepositStatus.FAILED
                    if isinstance(exc, PixProviderRejected)
                    else DepositStatus.REVIEW
                )
                await session.commit()
        raise

    if snapshot.external_reference != external_reference or snapshot.amount_cents != amount_cents:
        async with SessionFactory() as session:
            deposit = await session.get(Deposit, deposit_id)
            if deposit is not None:
                deposit.status = DepositStatus.REVIEW
                await session.commit()
        raise DepositMismatch("O pedido Pix retornado não corresponde ao depósito")

    final_status = DepositStatus.PENDING
    async with SessionFactory() as session:
        deposit = await session.scalar(
            select(Deposit).where(Deposit.id == deposit_id).with_for_update()
        )
        if deposit is None:
            raise LookupError("Depósito não encontrado")
        if (
            deposit.provider_order_id is not None
            and deposit.provider_order_id != snapshot.provider_order_id
        ):
            deposit.status = DepositStatus.REVIEW
            await session.commit()
            raise DepositMismatch("O identificador do pedido Pix mudou durante a criação")
        deposit.provider_order_id = snapshot.provider_order_id
        deposit.provider_payment_id = snapshot.payment_id
        deposit.provider_status = snapshot.status
        deposit.provider_status_detail = snapshot.status_detail
        deposit.ticket_url = snapshot.ticket_url
        deposit.qr_code = snapshot.qr_code
        deposit.qr_code_base64 = snapshot.qr_code_base64
        if deposit.status not in {
            DepositStatus.CREDITED,
            DepositStatus.REFUNDED,
            DepositStatus.CHARGED_BACK,
        }:
            deposit.status = DepositStatus.PENDING
        final_status = deposit.status
        await session.commit()

    if snapshot.status == "processed" and snapshot.status_detail == "accredited":
        result = await reconcile_snapshot(snapshot)
        if result.event == "credited":
            final_status = DepositStatus.CREDITED

    return DepositPayment(
        id=deposit_id,
        amount_cents=amount_cents,
        status=final_status,
        provider_order_id=snapshot.provider_order_id,
        ticket_url=snapshot.ticket_url,
        qr_code=snapshot.qr_code,
        qr_code_base64=snapshot.qr_code_base64,
        expires_at=expires_at,
    )


async def reconcile_snapshot(snapshot: PixOrderSnapshot) -> ReconcileResult:
    async with SessionFactory() as session:
        deposit = await session.scalar(
            select(Deposit)
            .where(
                or_(
                    Deposit.provider_order_id == snapshot.provider_order_id,
                    Deposit.external_reference == snapshot.external_reference,
                )
            )
            .with_for_update()
        )
        if deposit is None:
            raise LookupError("Depósito do webhook não encontrado")

        user = await session.get(User, deposit.user_id)
        if user is None:
            raise LookupError("Usuário do depósito não encontrado")

        if (
            snapshot.external_reference != deposit.external_reference
            or snapshot.amount_cents != deposit.amount_cents
        ):
            deposit.status = DepositStatus.REVIEW
            deposit.provider_status = snapshot.status
            deposit.provider_status_detail = snapshot.status_detail
            await session.commit()
            raise DepositMismatch("Referência ou valor do Pix diverge do depósito")

        if deposit.provider_order_id is None:
            deposit.provider_order_id = snapshot.provider_order_id
        elif deposit.provider_order_id != snapshot.provider_order_id:
            deposit.status = DepositStatus.REVIEW
            await session.commit()
            raise DepositMismatch("O identificador do pedido Pix diverge do depósito")

        deposit.provider_status = snapshot.status
        deposit.provider_status_detail = snapshot.status_detail
        deposit.provider_payment_id = snapshot.payment_id or deposit.provider_payment_id
        credited = snapshot.status == "processed" and snapshot.status_detail == "accredited"
        reversed_status = snapshot.status in {"refunded", "charged_back"}

        if credited and deposit.status != DepositStatus.CREDITED:
            wallet = await session.scalar(
                select(Wallet).where(Wallet.user_id == deposit.user_id).with_for_update()
            )
            if wallet is None:
                raise LookupError("Carteira não encontrada")
            wallet.balance_cents += deposit.amount_cents
            session.add(
                WalletEntry(
                    user_id=deposit.user_id,
                    entry_type=LedgerEntryType.DEPOSIT,
                    amount_cents=deposit.amount_cents,
                    balance_after_cents=wallet.balance_cents,
                    idempotency_key=f"deposit:{deposit.id}",
                    reference_type="deposit",
                    reference_id=str(deposit.id),
                    note="Crédito via Pix confirmado pelo provedor",
                )
            )
            deposit.status = DepositStatus.CREDITED
            deposit.credited_at = datetime.now(UTC)
            await session.commit()
            return ReconcileResult(
                changed=True,
                event="credited",
                telegram_id=user.telegram_id,
                amount_cents=deposit.amount_cents,
                balance_cents=wallet.balance_cents,
                deposit_id=deposit.id,
            )

        if reversed_status and deposit.status == DepositStatus.CREDITED:
            wallet = await session.scalar(
                select(Wallet).where(Wallet.user_id == deposit.user_id).with_for_update()
            )
            if wallet is None:
                raise LookupError("Carteira não encontrada")
            wallet.balance_cents -= deposit.amount_cents
            session.add(
                WalletEntry(
                    user_id=deposit.user_id,
                    entry_type=LedgerEntryType.REVERSAL,
                    amount_cents=-deposit.amount_cents,
                    balance_after_cents=wallet.balance_cents,
                    idempotency_key=f"deposit-reversal:{deposit.id}:{snapshot.status}",
                    reference_type="deposit",
                    reference_id=str(deposit.id),
                    note=f"Reversão informada pelo provedor: {snapshot.status}",
                )
            )
            deposit.status = (
                DepositStatus.REFUNDED
                if snapshot.status == "refunded"
                else DepositStatus.CHARGED_BACK
            )
            deposit.reversed_at = datetime.now(UTC)
            await session.commit()
            return ReconcileResult(
                changed=True,
                event="reversed",
                telegram_id=user.telegram_id,
                amount_cents=deposit.amount_cents,
                balance_cents=wallet.balance_cents,
                deposit_id=deposit.id,
            )

        mapped = {
            "expired": DepositStatus.EXPIRED,
            "canceled": DepositStatus.CANCELED,
            "failed": DepositStatus.FAILED,
        }.get(snapshot.status)
        if mapped and deposit.status not in {
            DepositStatus.CREDITED,
            DepositStatus.REFUNDED,
            DepositStatus.CHARGED_BACK,
        }:
            deposit.status = mapped
        elif snapshot.status == "processed" and snapshot.status_detail == "partially_refunded":
            deposit.status = DepositStatus.REVIEW
        await session.commit()
        return ReconcileResult(
            changed=False,
            event=deposit.status.value,
            telegram_id=user.telegram_id,
            amount_cents=deposit.amount_cents,
            balance_cents=None,
            deposit_id=deposit.id,
        )


async def reconcile_provider_order(
    provider_order_id: str, provider: PixProvider
) -> ReconcileResult:
    snapshot = await provider.get_order(provider_order_id)
    return await reconcile_snapshot(snapshot)


async def get_user_deposit(telegram_id: int, deposit_id: uuid.UUID) -> DepositLookup:
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(Deposit, User)
                .join(User, User.id == Deposit.user_id)
                .where(Deposit.id == deposit_id, User.telegram_id == telegram_id)
            )
        ).first()
        if row is None:
            raise LookupError("Depósito não encontrado")
        deposit, _user = row
        if not deposit.provider_order_id:
            raise LookupError("Depósito ainda não possui pedido no provedor")
        return DepositLookup(
            id=deposit.id,
            provider_order_id=deposit.provider_order_id,
            status=deposit.status,
            amount_cents=deposit.amount_cents,
        )


async def approve_mock_deposit(deposit_id: uuid.UUID) -> ReconcileResult:
    async with SessionFactory() as session:
        deposit = await session.get(Deposit, deposit_id)
        if deposit is None or deposit.provider != "mock" or not deposit.provider_order_id:
            raise LookupError("Depósito mock não encontrado")
        snapshot = PixOrderSnapshot(
            provider_order_id=deposit.provider_order_id,
            external_reference=deposit.external_reference,
            amount_cents=deposit.amount_cents,
            status="processed",
            status_detail="accredited",
            payment_id=deposit.provider_payment_id,
        )
    return await reconcile_snapshot(snapshot)


def _fallback_event_id(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


async def record_mercado_pago_event(payload: dict) -> bool:
    """Persiste o evento antes do HTTP 200. Retorna se ele precisa ser processado."""
    event_id = str(payload.get("id") or _fallback_event_id(payload))
    resource_id = str(payload.get("data", {}).get("id") or "")
    topic = str(payload.get("type") or "unknown")
    if topic != "order" or not resource_id:
        return False
    try:
        async with SessionFactory() as session:
            existing = await session.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "mercado_pago",
                    WebhookEvent.event_id == event_id,
                )
            )
            if existing is not None:
                return not existing.processed
            session.add(
                WebhookEvent(
                    provider="mercado_pago",
                    event_id=event_id,
                    topic=topic,
                    resource_id=resource_id,
                    payload=payload,
                )
            )
            await session.commit()
            return True
    except IntegrityError:
        # Inserção concorrente do mesmo evento: a outra transação já o tornou durável.
        return True


async def list_pending_mercado_pago_events(limit: int = 100) -> list[dict]:
    async with SessionFactory() as session:
        events = (
            await session.scalars(
                select(WebhookEvent)
                .where(
                    WebhookEvent.provider == "mercado_pago",
                    WebhookEvent.processed.is_(False),
                )
                .order_by(WebhookEvent.created_at)
                .limit(limit)
            )
        ).all()
        return [event.payload for event in events]


async def process_mercado_pago_event(
    payload: dict, provider: PixProvider
) -> ReconcileResult | None:
    event_id = str(payload.get("id") or _fallback_event_id(payload))
    resource_id = str(payload.get("data", {}).get("id") or "")
    topic = str(payload.get("type") or "unknown")

    if topic != "order" or not resource_id:
        return None

    if not await record_mercado_pago_event(payload):
        return None

    try:
        result = await reconcile_provider_order(resource_id, provider)
    except Exception as exc:
        async with SessionFactory() as session:
            event = await session.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.provider == "mercado_pago",
                    WebhookEvent.event_id == event_id,
                )
            )
            if event:
                event.error = str(exc)[:500]
                await session.commit()
        raise

    async with SessionFactory() as session:
        event = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.provider == "mercado_pago",
                WebhookEvent.event_id == event_id,
            )
        )
        if event:
            event.processed = True
            event.error = None
            event.processed_at = datetime.now(UTC)
            await session.commit()
    return result
