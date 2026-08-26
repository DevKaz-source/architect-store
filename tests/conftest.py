from __future__ import annotations

import os

import pytest_asyncio
from cryptography.fernet import Fernet

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())

from app import models  # noqa: E402, F401
from app.db import Base, engine  # noqa: E402


@pytest_asyncio.fixture
async def clean_database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
