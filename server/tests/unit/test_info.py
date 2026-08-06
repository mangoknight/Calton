"""T33 — GET /info and the feature flags."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from calton.api.v1.info import build_info, build_router

GOLDEN = Path(__file__).resolve().parents[2] / "contract" / "calton-v1-swagger.json"


def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_router(), prefix="/api/v1")
    return TestClient(app)


def test_concurrent_writes_is_false_on_sqlite() -> None:
    """The one flag that changes client behaviour rather than just UI: clients
    serialise batched writes when it is false. True would deadlock us."""
    assert build_info()["concurrent_writes"] is False


def test_every_unimplemented_feature_reports_false() -> None:
    """``webhooks_enabled`` used to be on this list and is not any more.

    It moved when Phase 2 implemented the four project-webhook routes — it is the only
    flag here that is *derived* rather than constant, so it belongs with the implemented
    features below rather than with the honest degradations.
    """
    info = build_info()
    for flag in (
        "caldav_enabled",
        "demo_mode_enabled",
        "email_reminders_enabled",
        "link_sharing_enabled",
        "public_teams_enabled",
        "totp_enabled",
        "user_deletion_enabled",
    ):
        assert info[flag] is False, flag


def test_the_features_phase_1_does_implement_report_true() -> None:
    info = build_info()
    assert info["task_attachments_enabled"] is True
    assert info["task_comments_enabled"] is True


def test_webhooks_enabled_follows_the_config_rather_than_a_constant() -> None:
    """⚠️ The only flag in this payload that is read rather than declared.

    Upstream derives it from ``webhooks.enabled`` — measured on both settings — and the
    two verification devices run the Go side on opposite ones: the parity harness turns
    webhooks off (``local_servers.SHARED_ENV``), the MCP gate leaves upstream's default
    on. So a constant here, either way round, is a divergence against one of them, and
    ``/info`` is compared on every parity case.

    ``build_info``'s default is **true**, matching ``config.go:489``; a caller that hands
    it the setting gets whichever plane it is running on.
    """
    assert build_info()["webhooks_enabled"] is True
    assert build_info(webhooks_enabled=False)["webhooks_enabled"] is False


def test_oidc_and_ldap_are_off_but_local_login_is_on() -> None:
    auth = build_info()["auth"]
    assert auth["local"]["enabled"] is True
    assert auth["ldap"]["enabled"] is False
    assert auth["openid_connect"]["enabled"] is False


def test_registration_enabled_is_nested_under_auth_local() -> None:
    """Measured against the running Go server: there is no top-level
    registration_enabled. The swagger alone gave the wrong shape."""
    info = build_info()
    assert info["auth"]["local"]["registration_enabled"] is True
    assert "registration_enabled" not in info


def test_openid_providers_is_null_not_an_empty_list() -> None:
    """Go leaves the slice nil, which encodes as null. An empty list is a
    different value that clients can branch on."""
    assert build_info()["auth"]["openid_connect"]["providers"] is None


def test_the_three_list_fields_copy_gos_inconsistency() -> None:
    """Measured against a running Go server: it does not treat these uniformly,
    and neither may we. enabled_background_providers is a nil slice when empty
    (JSON null); enabled_pro_features is a made slice ([])."""
    info = build_info()
    assert info["available_migrators"] == []
    assert info["enabled_background_providers"] is None
    assert info["enabled_pro_features"] == []


def test_field_names_are_a_superset_of_upstreams() -> None:
    """A client reading a field that vanished gets undefined, not an error."""
    spec = json.loads(GOLDEN.read_text())
    upstream = set(spec["definitions"]["shared.CaltonInfos"]["properties"])
    assert upstream - set(build_info()) == set()


def test_max_items_per_page_agrees_with_the_paginator() -> None:
    from calton.core.pagination import MAX_ITEMS_PER_PAGE

    assert build_info()["max_items_per_page"] == MAX_ITEMS_PER_PAGE


def test_the_endpoint_serves_it() -> None:
    resp = client().get("/api/v1/info")
    assert resp.status_code == 200
    assert resp.json()["concurrent_writes"] is False


def test_cache_control_is_no_store_via_the_real_app() -> None:
    """Applied by main.py's API-wide middleware, not by this router — Go sets it
    on every routed /api/v1 response, so a per-endpoint header would be wrong."""
    from fastapi.testclient import TestClient

    from calton.main import create_app

    resp = TestClient(create_app()).get("/api/v1/info")
    assert resp.headers["cache-control"] == "no-store"
