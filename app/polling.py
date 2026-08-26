from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.setup import build_dispatcher
from app.db import dispose_engine
from app.payments.factory import build_pix_provider
from app.services.supplier_jobs import run_supplier_reconciler
from app.settings import get_settings
from app.suppliers.factory import build_giftcard_supplier


async def run() -> None:
    settings = get_settings()
    settings.validate_runtime()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    provider = build_pix_provider(settings)
    supplier = build_giftcard_supplier(settings)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(settings, provider, supplier)
    await bot.delete_webhook(drop_pending_updates=False)
    supplier_task = (
        asyncio.create_task(
            run_supplier_reconciler(
                bot=bot,
                supplier=supplier,
                interval_seconds=settings.supplier_reconcile_seconds,
            )
        )
        if supplier is not None
        else None
    )
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        if supplier_task is not None:
            supplier_task.cancel()
            with suppress(asyncio.CancelledError):
                await supplier_task
        if supplier is not None:
            await supplier.aclose()
        await bot.session.close()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(run())
