from __future__ import annotations

import secrets

from cryptography.fernet import Fernet

if __name__ == "__main__":
    print(f"TELEGRAM_WEBHOOK_SECRET={secrets.token_urlsafe(32)}")
    print(f"DATA_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
