from __future__ import annotations

import base64
import binascii
import re
import uuid
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.money import InvalidMoney, format_brl, parse_brl_to_cents
from app.payments.base import PixProvider, PixProviderError
from app.services.payments import (
    DepositMismatch,
    create_deposit,
    get_user_deposit,
    reconcile_provider_order,
)
from app.services.users import get_user_by_telegram, set_user_email
from app.services.wallets import get_wallet_summary
from app.settings import Settings

router = Router(name="wallet")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class TopUpStates(StatesGroup):
    waiting_email = State()
    waiting_amount = State()


async def _show_balance(message: Message) -> None:
    if message.from_user is None:
        return
    summary = await get_wallet_summary(message.from_user.id)
    lines = [f"<b>Saldo: {format_brl(summary.balance_cents)}</b>"]
    if summary.entries:
        lines.append("\nÚltimos lançamentos:")
        for entry in summary.entries:
            sign = "+" if entry.amount_cents > 0 else ""
            note = escape((entry.note or "Lançamento")[:80], quote=False)
            lines.append(f"• {sign}{format_brl(entry.amount_cents)} · {note}")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Adicionar saldo via Pix", callback_data="topup:start")]
        ]
    )
    await message.answer("\n".join(lines), reply_markup=keyboard)


@router.message(Command("saldo"))
@router.message(F.text == "💰 Meu saldo")
async def balance(message: Message) -> None:
    await _show_balance(message)


@router.callback_query(F.data == "topup:start")
async def topup_start(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    user = await get_user_by_telegram(callback.from_user.id)
    if user is None or user.accepted_terms_at is None:
        await callback.answer("Use /start e aceite os termos", show_alert=True)
        return
    if user.is_blocked:
        await callback.answer("Conta bloqueada para operações financeiras", show_alert=True)
        return
    await callback.answer()
    if not callback.message:
        return
    if not user.email:
        await state.set_state(TopUpStates.waiting_email)
        await callback.message.answer(
            "Informe seu <b>e-mail</b>. Ele é exigido pelo provedor de pagamento e será "
            "salvo para os próximos depósitos."
        )
        return
    await state.set_state(TopUpStates.waiting_amount)
    await callback.message.answer(
        f"Quanto deseja adicionar?\n"
        f"Mínimo: <b>{format_brl(settings.min_topup_cents)}</b> · "
        f"Máximo: <b>{format_brl(settings.max_topup_cents)}</b>\n\n"
        "Exemplo: <code>25,00</code>"
    )


@router.message(TopUpStates.waiting_email)
async def receive_email(message: Message, state: FSMContext, settings: Settings) -> None:
    if message.from_user is None or not message.text:
        return
    email = message.text.strip().lower()
    if len(email) > 320 or not EMAIL_RE.fullmatch(email):
        await message.answer("Esse e-mail não parece válido. Tente novamente ou use /cancelar.")
        return
    await set_user_email(message.from_user.id, email)
    await state.set_state(TopUpStates.waiting_amount)
    await message.answer(
        f"E-mail salvo. Quanto deseja adicionar?\n"
        f"Mínimo: <b>{format_brl(settings.min_topup_cents)}</b> · "
        f"Máximo: <b>{format_brl(settings.max_topup_cents)}</b>"
    )


@router.message(TopUpStates.waiting_amount)
async def receive_amount(
    message: Message,
    state: FSMContext,
    settings: Settings,
    pix_provider: PixProvider,
) -> None:
    if message.from_user is None or not message.text:
        return
    try:
        amount_cents = parse_brl_to_cents(message.text)
    except InvalidMoney as exc:
        await message.answer(f"{escape(str(exc))}. Exemplo: <code>25,00</code>")
        return
    if not settings.min_topup_cents <= amount_cents <= settings.max_topup_cents:
        await message.answer(
            f"Use um valor entre {format_brl(settings.min_topup_cents)} e "
            f"{format_brl(settings.max_topup_cents)}."
        )
        return
    user = await get_user_by_telegram(message.from_user.id)
    if user is None or not user.email:
        await state.clear()
        await message.answer("Não encontrei seu e-mail. Inicie novamente em 💰 Meu saldo.")
        return

    await message.answer("Gerando seu Pix…")
    try:
        deposit = await create_deposit(
            user_id=user.id,
            payer_email=user.email,
            amount_cents=amount_cents,
            provider=pix_provider,
            settings=settings,
        )
    except ValueError as exc:
        await state.clear()
        await message.answer(escape(str(exc)))
        return
    except (PixProviderError, DepositMismatch):
        await state.clear()
        await message.answer(
            "O provedor não conseguiu gerar o Pix agora. Nenhum valor foi cobrado; "
            "tente novamente em alguns minutos."
        )
        return
    await state.clear()

    keyboard_rows = []
    if deposit.ticket_url:
        keyboard_rows.append(
            [InlineKeyboardButton(text="Abrir página do Pix", url=deposit.ticket_url)]
        )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="Já paguei · verificar", callback_data=f"pixcheck:{deposit.id.hex}"
            )
        ]
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    caption = (
        f"<b>Pix de {format_brl(deposit.amount_cents)}</b>\n"
        f"Válido por {settings.pix_expiration_minutes} minutos.\n"
        "O saldo só será liberado após a confirmação do provedor."
    )

    if deposit.qr_code_base64:
        try:
            raw = deposit.qr_code_base64.split(",")[-1]
            image = base64.b64decode(raw, validate=True)
            await message.answer_photo(
                BufferedInputFile(image, filename="pix.png"),
                caption=caption,
                reply_markup=keyboard,
            )
        except (ValueError, binascii.Error):
            await message.answer(caption, reply_markup=keyboard)
    else:
        await message.answer(caption, reply_markup=keyboard)
    if deposit.qr_code:
        await message.answer(f"<b>Pix Copia e Cola</b>\n<code>{escape(deposit.qr_code)}</code>")
    if pix_provider.name == "mock":
        await message.answer(
            f"🧪 Modo de teste: aprove com\n"
            f"<code>python -m app.cli approve-mock {deposit.id}</code>"
        )


@router.callback_query(F.data.startswith("pixcheck:"))
async def pix_check(callback: CallbackQuery, pix_provider: PixProvider) -> None:
    try:
        deposit_id = uuid.UUID(hex=(callback.data or "").split(":", 1)[1])
        deposit = await get_user_deposit(callback.from_user.id, deposit_id)
    except (ValueError, IndexError, LookupError):
        await callback.answer("Depósito não encontrado", show_alert=True)
        return
    if deposit.status.value == "credited":
        await callback.answer("Saldo já creditado", show_alert=True)
        return
    try:
        result = await reconcile_provider_order(deposit.provider_order_id, pix_provider)
    except PixProviderError:
        await callback.answer("Pagamento ainda não confirmado", show_alert=True)
        return
    if result.event == "credited" and result.balance_cents is not None:
        await callback.answer("Pagamento aprovado!", show_alert=True)
        if callback.message:
            await callback.message.answer(
                f"✅ Recebemos seu Pix de <b>{format_brl(result.amount_cents)}</b>.\n"
                f"Novo saldo: <b>{format_brl(result.balance_cents)}</b>"
            )
    else:
        await callback.answer("Pagamento ainda não confirmado", show_alert=True)


@router.message(Command("cancelar"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Operação cancelada.")
