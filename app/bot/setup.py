from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import admin, catalog, start, support, wallet
from app.payments.base import PixProvider
from app.settings import Settings
from app.suppliers.base import GiftCardSupplier


def build_dispatcher(
    settings: Settings,
    pix_provider: PixProvider,
    giftcard_supplier: GiftCardSupplier | None = None,
) -> Dispatcher:
    if settings.redis_url:
        storage = RedisStorage.from_url(
            settings.redis_url,
            state_ttl=1800,
            data_ttl=1800,
        )
        event_isolation = storage.create_isolation()
    else:
        storage = MemoryStorage()
        event_isolation = SimpleEventIsolation()
    dispatcher = Dispatcher(
        storage=storage,
        events_isolation=event_isolation,
        settings=settings,
        pix_provider=pix_provider,
        giftcard_supplier=giftcard_supplier,
    )
    dispatcher.include_router(start.router)
    dispatcher.include_router(wallet.router)
    dispatcher.include_router(catalog.router)
    dispatcher.include_router(support.router)
    dispatcher.include_router(admin.router)
    return dispatcher
