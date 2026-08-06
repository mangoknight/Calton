"""JWT issuance and verification, checked against a recorded Go reference.

The fixture is ``tests/fixtures/go_jwt.json``, produced by
``scripts/dump_go_jwt.py`` against a running Go server. Assertions read the
expected values *out of the fixture* rather than repeating them as literals, so a
reference that changes cannot leave a test passing against a stale expectation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt as pyjwt
import pytest

from calton.auth import jwt as jwt_auth
from calton.config import Settings

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "go_jwt.json"


@pytest.fixture(scope="module")
def go() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text())
    return loaded


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate({"service": {"secret": "testsecrettestsecrettestsecret12"}})


def user_token(settings: Settings, **overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "user_id": 900,
        "username": "alice",
        "is_admin": False,
        "session_id": "e6122e48-4202-4a80-9b90-730111f29ef1",
        "settings": settings,
    }
    kwargs.update(overrides)
    return jwt_auth.issue_user_token(**kwargs)


def claims_of(token: str) -> dict[str, Any]:
    decoded: dict[str, Any] = pyjwt.decode(token, options={"verify_signature": False})
    return decoded


class TestUserTokenClaims:
    def test_claim_names_match_the_go_reference_exactly(
        self, settings: Settings, go: dict[str, Any]
    ) -> None:
        """Not a subset check: an extra claim diverges as surely as a missing one."""
        assert sorted(claims_of(user_token(settings))) == go["user_token"]["claim_keys"]

    def test_there_is_no_iat_claim(self, settings: Settings, go: dict[str, Any]) -> None:
        """The design's "exp - iat == TTL" criterion is not checkable: no iat exists.

        Pinned as its own assertion because PyJWT will happily add one if a future
        edit passes `iat` through, and nothing else here would notice.
        """
        assert "iat" not in claims_of(user_token(settings))
        assert "iat" not in go["user_token"]["claim_keys"]

    def test_claim_values_have_the_go_types(self, settings: Settings, go: dict[str, Any]) -> None:
        reference = go["user_token"]["claims_sample"]
        produced = claims_of(user_token(settings))

        assert {name: type(value) for name, value in produced.items()} == {
            name: type(value) for name, value in reference.items()
        }

    def test_type_claim_is_1(self, settings: Settings, go: dict[str, Any]) -> None:
        assert claims_of(user_token(settings))["type"] == go["user_token"]["claims_sample"]["type"]

    def test_header_matches_the_reference(self, settings: Settings, go: dict[str, Any]) -> None:
        header = pyjwt.get_unverified_header(user_token(settings))
        assert header == go["user_token"]["header"]

    def test_jti_is_new_on_every_issuance(self, settings: Settings) -> None:
        first = claims_of(user_token(settings))["jti"]
        second = claims_of(user_token(settings))["jti"]
        assert first != second

    def test_sid_is_the_session_it_was_issued_for(self, settings: Settings) -> None:
        token = user_token(settings, session_id="a-session-id")
        assert claims_of(token)["sid"] == "a-session-id"


class TestTokenLifetimes:
    def test_user_token_expires_after_the_configured_short_ttl(
        self, settings: Settings, go: dict[str, Any]
    ) -> None:
        """Measured against issuance time, since there is no iat to subtract."""
        now = datetime(2026, 1, 1, tzinfo=UTC)
        token = jwt_auth.issue_user_token(
            user_id=1,
            username="alice",
            is_admin=False,
            session_id="s",
            settings=settings,
            now=now,
        )

        elapsed = claims_of(token)["exp"] - int(now.timestamp())
        assert elapsed == settings.service.jwtttlshort
        assert elapsed == go["user_token"]["ttl_seconds_approx"]

    def test_the_ttl_comes_from_configuration_not_a_constant(self) -> None:
        """G05 turns jwtttlshort down to 5s to test expiry races; a hard-coded 600
        would silently ignore that and the race test would never fire."""
        settings = Settings.model_validate(
            {"service": {"secret": "another-secret-of-at-least-32-bytes", "jwtttlshort": 5}}
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)

        token = jwt_auth.issue_user_token(
            user_id=1, username="a", is_admin=False, session_id="s", settings=settings, now=now
        )

        assert claims_of(token)["exp"] - int(now.timestamp()) == 5

    def test_link_share_token_uses_the_long_ttl_not_the_short_one(
        self, settings: Settings, go: dict[str, Any]
    ) -> None:
        """The two token types have different lifetimes — 72h against 600s."""
        now = datetime(2026, 1, 1, tzinfo=UTC)
        token = jwt_auth.issue_link_share_token(
            share_id=5,
            share_hash="abc",
            project_id=900,
            permission=0,
            shared_by_id=900,
            settings=settings,
            now=now,
        )

        elapsed = claims_of(token)["exp"] - int(now.timestamp())
        assert elapsed == settings.service.jwtttl
        assert elapsed == go["link_share_token"]["ttl_seconds_approx"]
        assert elapsed != settings.service.jwtttlshort


class TestLinkShareClaims:
    @pytest.fixture
    def token(self, settings: Settings) -> str:
        return jwt_auth.issue_link_share_token(
            share_id=5,
            share_hash="ZFHn38WzN75Qxhew2J0Ui5dBcyfVzXAWARsMmQtw",
            project_id=900,
            permission=0,
            shared_by_id=900,
            settings=settings,
        )

    def test_claim_names_match_the_go_reference_exactly(
        self, token: str, go: dict[str, Any]
    ) -> None:
        assert sorted(claims_of(token)) == go["link_share_token"]["claim_keys"]

    def test_shared_by_id_is_camel_case(self, token: str) -> None:
        """The one camelCase key in the API. Copied because clients read it."""
        claims = claims_of(token)
        assert "sharedByID" in claims
        assert "shared_by_id" not in claims

    def test_permission_is_the_claim_name_not_right(self, token: str) -> None:
        assert "permission" in claims_of(token)


class TestVerification:
    def test_a_token_it_issued_verifies(self, settings: Settings) -> None:
        claims = jwt_auth.decode(user_token(settings), settings)
        assert claims["username"] == "alice"

    def test_a_tampered_signature_is_rejected(self, settings: Settings) -> None:
        header, payload, signature = user_token(settings).split(".")
        # The first signature character, not the last: base64url's final character
        # carries spare bits, so changing it can decode to the same bytes and the
        # token still verifies — a mutation that looks like tampering but is not.
        flipped = ("B" if signature[0] != "B" else "C") + signature[1:]

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(f"{header}.{payload}.{flipped}", settings)

    def test_a_tampered_payload_is_rejected(self, settings: Settings) -> None:
        """The attack that matters: promoting yourself to admin in the claims."""
        header, _, signature = user_token(settings).split(".")
        forged_claims = pyjwt.encode(
            {**claims_of(user_token(settings)), "is_admin": True},
            settings.service.secret,
            algorithm=jwt_auth.ALGORITHM,
        ).split(".")[1]

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(f"{header}.{forged_claims}.{signature}", settings)

    def test_a_token_signed_with_another_secret_is_rejected(self, settings: Settings) -> None:
        other = Settings.model_validate(
            {"service": {"secret": "a-completely-different-secret-32-bytes"}}
        )
        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(user_token(other), settings)

    def test_an_expired_token_is_rejected(self, settings: Settings) -> None:
        past = datetime.now(UTC) - timedelta(seconds=settings.service.jwtttlshort + 60)
        token = user_token(settings, now=past)

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(token, settings)

    def test_a_token_signed_with_a_different_algorithm_is_rejected(
        self, settings: Settings
    ) -> None:
        """What the algorithm pin actually buys.

        Accepting any algorithm the header names lets an attacker who learns the
        HMAC secret pick their own; more practically it lets a token minted for
        one purpose be replayed against another. Widening ``algorithms`` to
        include HS512 makes this pass, which is what makes the pin load-bearing —
        unlike the alg=none case below, which PyJWT refuses on its own.
        """
        # Signed with the *real* secret, so widening `algorithms` genuinely admits
        # it rather than tripping over a signature mismatch. The secret is 64 bytes
        # only because PyJWT warns below that length for SHA512, and the suite
        # turns warnings into errors.
        long_secret = Settings.model_validate({"service": {"secret": "s" * 64}})
        other_alg = pyjwt.encode(
            {"type": 1, "id": 900, "exp": 9999999999},
            long_secret.service.secret,
            algorithm="HS512",
        )

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(other_alg, long_secret)

    def test_an_unsigned_alg_none_token_is_rejected(self, settings: Settings) -> None:
        """The classic JWT forgery.

        ⚠️ Belt and braces, not the sole guard: PyJWT 2.13 refuses ``alg=none``
        whenever a non-empty key is passed, so this stays green even if the
        algorithm pin is widened. Kept because the pin is the layer we control.
        """
        forged = pyjwt.encode(
            {"type": 1, "id": 900, "username": "alice", "is_admin": True, "exp": 9999999999},
            key="",
            algorithm="none",
        )

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(forged, settings)

    def test_a_token_with_no_exp_is_rejected(self, settings: Settings) -> None:
        """A never-expiring token would survive any password change or logout."""
        forever = pyjwt.encode(
            {"type": 1, "id": 900}, settings.service.secret, algorithm=jwt_auth.ALGORITHM
        )

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(forever, settings)

    def test_an_unknown_type_claim_is_rejected(self, settings: Settings) -> None:
        odd = pyjwt.encode(
            {"type": 99, "id": 1, "exp": 9999999999},
            settings.service.secret,
            algorithm=jwt_auth.ALGORITHM,
        )

        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode(odd, settings)

    def test_garbage_is_rejected_rather_than_raising_something_else(
        self, settings: Settings
    ) -> None:
        with pytest.raises(jwt_auth.InvalidTokenError):
            jwt_auth.decode("not-a-token", settings)
