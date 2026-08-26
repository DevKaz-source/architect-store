from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


class InvalidCiphertext(ValueError):
    pass


class SecretBox:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("Chave de criptografia ausente")
        self._fernet = Fernet(key.encode())
        self._fingerprint_key = hashlib.sha256(key.encode() + b":stock-fingerprint").digest()

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise InvalidCiphertext("Não foi possível descriptografar o conteúdo") from exc

    def fingerprint(self, plaintext: str) -> str:
        return hmac.new(
            self._fingerprint_key,
            plaintext.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


@dataclass(frozen=True)
class MercadoPagoSignature:
    timestamp: str
    digest: str


def parse_mercado_pago_signature(header: str) -> MercadoPagoSignature:
    parts: dict[str, str] = {}
    for item in header.split(","):
        key, separator, value = item.strip().partition("=")
        if separator and key and value:
            parts[key] = value
    if "ts" not in parts or "v1" not in parts:
        raise ValueError("Assinatura incompleta")
    return MercadoPagoSignature(timestamp=parts["ts"], digest=parts["v1"])


def mercado_pago_manifest(data_id: str | None, request_id: str | None, timestamp: str) -> str:
    fields: list[str] = []
    if data_id:
        fields.append(f"id:{data_id.lower()};")
    if request_id:
        fields.append(f"request-id:{request_id};")
    fields.append(f"ts:{timestamp};")
    return "".join(fields)


def verify_mercado_pago_signature(
    *,
    signature_header: str,
    request_id: str | None,
    data_id: str | None,
    secret: str,
) -> bool:
    if not signature_header or not secret:
        return False
    try:
        signature = parse_mercado_pago_signature(signature_header)
    except ValueError:
        return False
    manifest = mercado_pago_manifest(data_id, request_id, signature.timestamp)
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.digest)
