from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager, suppress

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app import __version__
from app.bot.presentation import configure_bot_commands
from app.bot.setup import build_dispatcher
from app.db import SessionFactory, dispose_engine
from app.money import format_brl
from app.payments.base import PixProvider
from app.payments.factory import build_pix_provider
from app.security import verify_mercado_pago_signature
from app.services.payments import (
    list_pending_mercado_pago_events,
    process_mercado_pago_event,
    record_mercado_pago_event,
)
from app.services.supplier_jobs import run_supplier_reconciler
from app.settings import get_settings
from app.suppliers.factory import build_giftcard_supplier

logger = logging.getLogger(__name__)
settings = get_settings()


async def _process_telegram_update(app: FastAPI, update: Update) -> None:
    try:
        await app.state.dispatcher.feed_update(app.state.bot, update)
    except Exception:
        logger.exception("Falha ao processar atualização do Telegram")


async def _process_payment_notification(*, bot: Bot, payload: dict, provider: PixProvider) -> None:
    try:
        result = await process_mercado_pago_event(payload, provider)
    except Exception:
        logger.exception("Falha ao conciliar notificação do Mercado Pago")
        return
    if result is None or not result.changed:
        return
    try:
        if result.event == "credited" and result.balance_cents is not None:
            await bot.send_message(
                result.telegram_id,
                f"✅ Recebemos seu Pix de <b>{format_brl(result.amount_cents)}</b>.\n"
                f"Novo saldo: <b>{format_brl(result.balance_cents)}</b>",
            )
        elif result.event == "reversed":
            await bot.send_message(
                result.telegram_id,
                "⚠️ Um Pix foi estornado ou contestado. O crédito correspondente "
                "foi revertido; procure o suporte se precisar de ajuda.",
            )
    except Exception:
        logger.exception("Falha ao avisar usuário sobre conciliação Pix")


async def _payment_retry_loop(app: FastAPI) -> None:
    while True:
        try:
            for payload in await list_pending_mercado_pago_events():
                await _process_payment_notification(
                    bot=app.state.bot,
                    payload=payload,
                    provider=app.state.pix_provider,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falha ao reprocessar webhooks pendentes")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    pix_provider = build_pix_provider(settings)
    giftcard_supplier = build_giftcard_supplier(settings)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(settings, pix_provider, giftcard_supplier)
    redis = Redis.from_url(settings.redis_url) if settings.redis_url else None

    app.state.bot = bot
    app.state.dispatcher = dispatcher
    app.state.pix_provider = pix_provider
    app.state.giftcard_supplier = giftcard_supplier
    app.state.redis = redis
    await dispatcher.emit_startup(bot=bot)

    await configure_bot_commands(bot)
    if settings.telegram_mode == "webhook":
        await bot.set_webhook(
            settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )

    background_tasks: list[asyncio.Task] = []
    if settings.pix_provider == "mercado_pago":
        background_tasks.append(asyncio.create_task(_payment_retry_loop(app)))
    if giftcard_supplier is not None:
        background_tasks.append(
            asyncio.create_task(
                run_supplier_reconciler(
                    bot=bot,
                    supplier=giftcard_supplier,
                    interval_seconds=settings.supplier_reconcile_seconds,
                )
            )
        )

    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task
        await dispatcher.emit_shutdown(bot=bot)
        if giftcard_supplier is not None:
            await giftcard_supplier.aclose()
        if redis is not None:
            await redis.aclose()
        await bot.session.close()
        await dispose_engine()


app = FastAPI(
    title="Architect Store Bot",
    version=__version__,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request) -> dict[str, str]:
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        redis: Redis | None = request.app.state.redis
        if redis is not None:
            await redis.ping()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dependência indisponível",
        ) from exc
    return {"status": "ready"}


@app.post("/webhooks/telegram", include_in_schema=False)
async def telegram_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, settings.telegram_webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="segredo inválido")
    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": request.app.state.bot})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="update inválido"
        ) from exc
    background_tasks.add_task(_process_telegram_update, request.app, update)
    return JSONResponse({"ok": True})


@app.post("/webhooks/mercado-pago", include_in_schema=False)
async def mercado_pago_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    if settings.pix_provider != "mercado_pago":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provedor inativo")
    data_id = request.query_params.get("data.id")
    request_id = request.headers.get("x-request-id")
    if not data_id or not request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="identificadores assinados ausentes",
        )
    valid = verify_mercado_pago_signature(
        signature_header=request.headers.get("x-signature", ""),
        request_id=request_id,
        data_id=data_id,
        secret=settings.mercado_pago_webhook_secret,
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="assinatura inválida")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="JSON inválido"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="evento inválido")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    body_resource_id = str(data.get("id") or "")
    if body_resource_id and data_id.lower() != body_resource_id.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="identificador do recurso divergente",
        )

    try:
        should_process = await record_mercado_pago_event(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="falha temporária de persistência",
        ) from exc
    if should_process:
        background_tasks.add_task(
            _process_payment_notification,
            bot=request.app.state.bot,
            payload=payload,
            provider=request.app.state.pix_provider,
        )
    return JSONResponse({"received": True})
