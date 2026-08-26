from __future__ import annotations

import secrets
import uuid
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import catalog_keyboard
from app.models import OrderStatus
from app.money import format_brl
from app.services.catalog import (
    InsufficientBalance,
    OutOfStock,
    ProductUnavailable,
    TermsRequired,
    UserBlocked,
    get_catalog_item,
    list_catalog,
    list_user_orders,
    mark_supplier_customer_notified,
    purchase_product,
    reveal_order,
)
from app.suppliers.base import GiftCardSupplier

router = Router(name="catalog")

ORDER_ICONS = {
    OrderStatus.DELIVERED: "✅",
    OrderStatus.REFUNDED: "↩️",
    OrderStatus.PROCESSING: "⏳",
    OrderStatus.REVIEW: "⚠️",
    OrderStatus.DISPUTED: "⚠️",
}


async def show_catalog(message: Message) -> None:
    items = await list_catalog()
    if not items:
        await message.answer("O catálogo está sendo atualizado. Volte em alguns minutos.")
        return
    await message.answer(
        "<b>Catálogo</b>\nEscolha um produto:", reply_markup=catalog_keyboard(items)
    )


@router.message(Command("catalogo"))
@router.message(F.text == "🛍 Catálogo")
async def catalog(message: Message) -> None:
    await show_catalog(message)


@router.callback_query(F.data.startswith("product:"))
async def product_details(callback: CallbackQuery) -> None:
    try:
        product_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Produto inválido", show_alert=True)
        return
    item = await get_catalog_item(product_id)
    if item is None:
        await callback.answer("Produto indisponível", show_alert=True)
        return
    nonce = secrets.token_hex(4)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Comprar por {format_brl(item.price_cents)}",
                    callback_data=f"buy:{item.id}:{nonce}",
                )
            ]
        ]
    )
    if callback.message:
        await callback.message.answer(
            f"<b>{escape(item.name)}</b>\n\n"
            f"{escape(item.description, quote=False)}\n\n"
            f"Preço: <b>{format_brl(item.price_cents)}</b>\n"
            f"Disponibilidade: <b>{item.availability_label}</b>\n\n"
            "Ao confirmar, o valor é debitado. Produtos de fornecedor normalmente "
            "são entregues em segundos; pedidos pendentes entram em conciliação.",
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery, giftcard_supplier: GiftCardSupplier | None) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Compra inválida", show_alert=True)
        return
    try:
        product_id = int(parts[1])
    except ValueError:
        await callback.answer("Produto inválido", show_alert=True)
        return
    idempotency_key = f"tg:{callback.from_user.id}:{product_id}:{parts[2]}"
    await callback.answer("Processando…")
    try:
        result = await purchase_product(
            telegram_id=callback.from_user.id,
            product_id=product_id,
            idempotency_key=idempotency_key,
            supplier=giftcard_supplier,
        )
    except InsufficientBalance as exc:
        if callback.message:
            missing = exc.price_cents - exc.balance_cents
            await callback.message.answer(
                f"Saldo insuficiente. Faltam <b>{format_brl(missing)}</b>. "
                "Use <b>💰 Meu saldo</b> para adicionar crédito via Pix."
            )
        return
    except OutOfStock:
        if callback.message:
            await callback.message.answer("Esse produto acabou de esgotar.")
        return
    except TermsRequired:
        if callback.message:
            await callback.message.answer("Use /start e aceite os termos antes da compra.")
        return
    except (ProductUnavailable, UserBlocked) as exc:
        if callback.message:
            await callback.message.answer(escape(str(exc)))
        return

    if callback.message:
        if callback.message.reply_markup:
            await callback.message.edit_reply_markup(reply_markup=None)
        repeated = "\n<i>Esta compra já havia sido concluída.</i>" if result.repeated else ""
        if result.status == OrderStatus.REFUNDED:
            await callback.message.answer(
                f"⚠️ <b>Compra não concluída</b>\n"
                f"Pedido: <code>{result.public_code}</code>\n"
                f"O fornecedor rejeitou a operação e o valor voltou automaticamente.\n"
                f"Saldo: <b>{format_brl(result.balance_cents)}</b>"
            )
            await mark_supplier_customer_notified(result.order_id)
            return
        if result.status in {OrderStatus.PROCESSING, OrderStatus.REVIEW}:
            await callback.message.answer(
                f"⏳ <b>Pedido em processamento</b>\n"
                f"Pedido: <code>{result.public_code}</code>\n"
                f"Produto: {escape(result.product_name)}\n"
                f"Saldo restante: <b>{format_brl(result.balance_cents)}</b>\n\n"
                "A compra não será repetida. Estamos conciliando com o fornecedor; "
                "acompanhe em <b>📦 Minhas compras</b> ou abra um chamado se demorar."
            )
            return
        await callback.message.answer(
            f"✅ <b>Compra concluída</b>\n"
            f"Pedido: <code>{result.public_code}</code>\n"
            f"Produto: {escape(result.product_name)}\n"
            f"Saldo restante: <b>{format_brl(result.balance_cents)}</b>{repeated}\n\n"
            "A entrega será enviada na próxima mensagem.",
        )
        if result.delivery:
            await callback.message.answer(
                result.delivery,
                parse_mode=None,
                protect_content=True,
            )
        await callback.message.answer(
            "Guarde os dados em local seguro. Em caso de problema, abra um chamado.",
        )
        await mark_supplier_customer_notified(result.order_id)


@router.message(Command("compras"))
@router.message(F.text == "📦 Minhas compras")
async def purchases(message: Message) -> None:
    if message.from_user is None:
        return
    orders = await list_user_orders(message.from_user.id)
    if not orders:
        await message.answer("Você ainda não fez nenhuma compra.")
        return
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{ORDER_ICONS.get(order.status, '⏳')} "
                    f"{order.public_code} · {order.product_name}"
                ),
                callback_data=f"order:{order.id.hex}",
            )
        ]
        for order in orders
    ]
    await message.answer(
        "<b>Suas compras recentes</b>\nToque para visualizar a entrega:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("order:"))
async def order_reveal(callback: CallbackQuery) -> None:
    try:
        order_id = uuid.UUID(hex=(callback.data or "").split(":", 1)[1])
        result = await reveal_order(callback.from_user.id, order_id)
    except (ValueError, IndexError, LookupError):
        await callback.answer("Pedido não encontrado", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"<b>{result.public_code} · {escape(result.product_name)}</b>"
        )
        if result.status == OrderStatus.REFUNDED:
            await callback.message.answer(
                "Esta compra foi reembolsada. O crédito já voltou para seu saldo."
            )
            return
        if result.status in {OrderStatus.PROCESSING, OrderStatus.REVIEW} or not result.delivery:
            await callback.message.answer(
                "O fornecedor ainda está processando este pedido. Não faça outra compra "
                "do mesmo item; a conciliação preserva seu pagamento."
            )
            return
        await callback.message.answer(
            result.delivery,
            parse_mode=None,
            protect_content=True,
        )
