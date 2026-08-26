from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot

from app.models import OrderStatus
from app.money import format_brl
from app.services.catalog import (
    list_reconcilable_supplier_orders,
    list_supplier_notifications,
    mark_supplier_customer_notified,
    reconcile_supplier_order,
)
from app.suppliers.base import GiftCardSupplier

logger = logging.getLogger(__name__)


async def _notify_finished_orders(bot: Bot, supplier: GiftCardSupplier) -> None:
    for notification in await list_supplier_notifications(supplier):
        result = notification.result
        try:
            if result.status == OrderStatus.DELIVERED and result.delivery:
                await bot.send_message(
                    notification.telegram_id,
                    f"✅ <b>Seu pedido ficou pronto</b>\n"
                    f"Pedido: <code>{result.public_code}</code>\n"
                    f"Produto: {escape(result.product_name)}\n\n"
                    "A entrega será enviada na próxima mensagem.",
                )
                await bot.send_message(
                    notification.telegram_id,
                    result.delivery,
                    parse_mode=None,
                    protect_content=True,
                )
            elif result.status == OrderStatus.REFUNDED:
                await bot.send_message(
                    notification.telegram_id,
                    f"⚠️ <b>Pedido reembolsado</b>\n"
                    f"Pedido: <code>{result.public_code}</code>\n"
                    f"O fornecedor não concluiu a operação. "
                    f"Seu saldo atual é <b>{format_brl(result.balance_cents)}</b>.",
                )
            else:
                continue
        except Exception:
            logger.exception("Falha ao avisar cliente sobre pedido %s", result.public_code)
            continue
        await mark_supplier_customer_notified(result.order_id)


async def run_supplier_reconciler(
    *, bot: Bot, supplier: GiftCardSupplier, interval_seconds: int
) -> None:
    while True:
        try:
            for public_code in await list_reconcilable_supplier_orders(supplier):
                try:
                    await reconcile_supplier_order(public_code, supplier)
                except Exception:
                    logger.exception("Falha ao conciliar pedido %s", public_code)
            await _notify_finished_orders(bot, supplier)
        except Exception:
            logger.exception("Falha no ciclo de conciliação de fornecedores")
        await asyncio.sleep(interval_seconds)
