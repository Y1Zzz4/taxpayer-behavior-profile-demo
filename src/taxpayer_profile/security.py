"""Phone normalization, keyed lookup hashes, and reversible encryption."""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

PHONE_SEPARATORS = re.compile(r"[\s\-－—()（）]")
EMAIL_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])"
)
ID_CARD_PATTERN = re.compile(r"(?<![0-9A-Z])\d{17}[0-9Xx](?![0-9A-Z])")
MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ACCOUNT_PATTERN = re.compile(
    r"((?:账号|账户|卡号)\s*[:：]?\s*)[A-Za-z0-9_-]{6,}", re.IGNORECASE
)
CREDIT_CODE_PATTERN = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z])")
FIXED_PHONE_PATTERN = re.compile(r"(?<!\d)(?:0\d{2,3}[-－— ]?)?\d{7,8}(?!\d)")
LONG_NUMERIC_PATTERN = re.compile(r"(?<!\d)\d{12,}(?!\d)")


def redact_sensitive_text(value: object) -> str | None:
    """Mask common explicit identifiers before text leaves the local process."""

    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    redacted = EMAIL_PATTERN.sub("<邮箱>", value)
    redacted = ID_CARD_PATTERN.sub("<身份证号>", redacted)
    redacted = ACCOUNT_PATTERN.sub(r"\1<账号>", redacted)
    redacted = CREDIT_CODE_PATTERN.sub("<社会信用代码>", redacted)
    redacted = MOBILE_PATTERN.sub("<手机号>", redacted)
    redacted = FIXED_PHONE_PATTERN.sub("<固定电话>", redacted)
    redacted = LONG_NUMERIC_PATTERN.sub("<长数字标识>", redacted)
    return redacted


def normalize_phone(value: object) -> str | None:
    """Normalize a recorded numeric phone identifier without assuming its length."""

    if value is None or isinstance(value, (bool, float)):
        return None
    rendered = unicodedata.normalize("NFKC", str(value)).strip()
    if not rendered:
        return None
    normalized = PHONE_SEPARATORS.sub("", rendered)
    if not normalized.isascii() or not normalized.isdigit():
        return None
    return normalized


@dataclass(frozen=True)
class PhoneProtector:
    """Protect phone numbers using HMAC-SHA256 and Fernet."""

    hash_key: str
    encryption_key: str
    _fernet: Fernet = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.hash_key:
            raise ValueError("PHONE_HASH_KEY 不能为空")
        try:
            fernet = Fernet(self.encryption_key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("PHONE_ENCRYPTION_KEY 必须是有效的 Fernet 密钥") from exc
        object.__setattr__(self, "_fernet", fernet)

    def hash_phone(self, phone: object) -> str:
        normalized = normalize_phone(phone)
        if normalized is None:
            raise ValueError("来电号码必须为数字，可包含常见空格、横线或括号")
        return hmac.new(
            self.hash_key.encode("utf-8"),
            normalized.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def encrypt_phone(self, phone: object) -> str:
        normalized = normalize_phone(phone)
        if normalized is None:
            raise ValueError("来电号码必须为数字，可包含常见空格、横线或括号")
        return self._fernet.encrypt(normalized.encode("utf-8")).decode("ascii")

    def decrypt_phone(self, encrypted_phone: str) -> str:
        try:
            return self._fernet.decrypt(encrypted_phone.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise ValueError("电话号码密文无法使用当前密钥解密") from exc


def phone_log_label(phone_hash: str) -> str:
    """Return a short irreversible identifier suitable for logs."""

    return f"phone:{phone_hash[:12]}"
