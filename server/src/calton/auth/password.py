"""Password hashing — bcrypt, matching upstream exactly.

Copying the algorithm rather than moving to argon2id is a deliberate call (design
§2.1, user's decision): it means a real Calton SQLite file can be dropped
straight into Calton and every existing user can still log in, and it lets the
parity harness seed both servers from one snapshot.

⚠️ bcrypt truncates at 72 **bytes**, not characters. Upstream caps the field at
72 in validation and lets the library truncate; we do the same rather than
raising, because rejecting a password Calton accepted would lock users out of
an imported database.
"""

from __future__ import annotations

import bcrypt

# config.ServiceBcryptRounds, pkg/config/config.go:372.
BCRYPT_ROUNDS = 11

# The bcrypt limit. Anything past this byte is ignored by the algorithm itself.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str, rounds: int = BCRYPT_ROUNDS) -> str:
    """Hash a password. The result verifies against upstream and vice versa."""
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt(rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Check a password against a stored hash, including hashes Calton wrote."""
    try:
        return bcrypt.checkpw(_encode(password), hashed.encode())
    except ValueError:
        # A malformed or non-bcrypt hash is a failed login, not a crash.
        return False


def _encode(password: str) -> bytes:
    """UTF-8 bytes, truncated at bcrypt's 72-byte limit as the library does."""
    return password.encode()[:MAX_PASSWORD_BYTES]
