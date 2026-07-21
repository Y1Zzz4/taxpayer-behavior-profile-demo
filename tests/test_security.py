from cryptography.fernet import Fernet
import pytest

from taxpayer_profile.security import PhoneProtector, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("138 0000 0001", "13800000001"),
        ("1234-5678", "12345678"),
        ("1234567", "1234567"),
        ("021-1234567", "0211234567"),
        ("12345", "12345"),
        ("123", "123"),
        (13800000001, "13800000001"),
    ],
)
def test_normalize_phone(raw: object, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "热线A123", "13800000001.0"])
def test_normalize_phone_rejects_invalid_values(raw: object) -> None:
    assert normalize_phone(raw) is None


def test_phone_hmac_is_stable_and_keyed() -> None:
    encryption_key = Fernet.generate_key().decode()
    first = PhoneProtector("first-test-key", encryption_key)
    second = PhoneProtector("second-test-key", encryption_key)

    assert first.hash_phone("13800000001") == first.hash_phone("138 0000 0001")
    assert first.hash_phone("13800000001") != second.hash_phone("13800000001")
    assert "13800000001" not in first.hash_phone("13800000001")


def test_phone_encryption_round_trip() -> None:
    protector = PhoneProtector("test-hash-key", Fernet.generate_key().decode())

    encrypted = protector.encrypt_phone("12345678")

    assert encrypted != "12345678"
    assert protector.decrypt_phone(encrypted) == "12345678"
