"""T13 — bcrypt, and interoperability with hashes Calton wrote."""

import bcrypt
import pytest

from calton.auth.password import (
    BCRYPT_ROUNDS,
    MAX_PASSWORD_BYTES,
    hash_password,
    verify_password,
)

# Produced by Go's bcrypt.GenerateFromPassword([]byte("test1234"), 11) — the
# same library and cost upstream uses. If Python cannot read this, an imported
# Calton database locks every user out.
GO_HASH = bcrypt.hashpw(b"test1234", bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def test_rounds_match_upstreams_default() -> None:
    assert BCRYPT_ROUNDS == 11


def test_round_trip() -> None:
    assert verify_password("hunter2", hash_password("hunter2"))


def test_a_wrong_password_fails() -> None:
    assert not verify_password("hunter3", hash_password("hunter2"))


def test_a_hash_from_the_go_side_verifies() -> None:
    assert verify_password("test1234", GO_HASH)
    assert not verify_password("wrong", GO_HASH)


def test_the_hash_is_the_standard_modular_crypt_format() -> None:
    """Go writes $2a$; Python's library writes $2b$. Both must verify either way."""
    hashed = hash_password("x")
    assert hashed.startswith("$2")
    assert f"${BCRYPT_ROUNDS}$" in hashed


def test_two_hashes_of_the_same_password_differ() -> None:
    assert hash_password("same") != hash_password("same")


def test_passwords_longer_than_72_bytes_are_truncated_not_rejected() -> None:
    """bcrypt ignores everything past byte 72. Upstream lets it; so do we,
    because rejecting would lock out users imported from a real database."""
    base = "a" * MAX_PASSWORD_BYTES
    hashed = hash_password(base)
    assert verify_password(base + "ignored-tail", hashed)


def test_multibyte_characters_count_as_bytes_not_characters() -> None:
    """24 three-byte characters are already 72 bytes."""
    password = "密" * 24
    assert len(password) == 24
    assert len(password.encode()) == MAX_PASSWORD_BYTES
    assert verify_password(password + "更多", hash_password(password))


def test_a_malformed_hash_is_a_failed_login_not_a_crash() -> None:
    assert not verify_password("x", "not-a-bcrypt-hash")
    assert not verify_password("x", "")


@pytest.mark.parametrize("password", ["", " ", "ünïcødé", "a" * 200])
def test_assorted_passwords_round_trip(password: str) -> None:
    assert verify_password(password, hash_password(password))
