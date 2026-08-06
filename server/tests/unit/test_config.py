import pytest

from calton.config import Settings, get_settings


def test_defaults_match_upstream() -> None:
    settings = Settings()

    assert settings.service.maxitemsperpage == 50
    assert settings.service.jwtttlshort == 600
    assert settings.service.bcryptrounds == 11
    assert settings.service.timezone == "GMT"
    assert settings.database.type == "sqlite"
    assert settings.files.maxsize == "20MB"


def test_upstream_environment_variables_are_understood(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALTON_SERVICE_SECRET", "s3cret")
    monkeypatch.setenv("CALTON_SERVICE_MAXITEMSPERPAGE", "25")
    monkeypatch.setenv("CALTON_DATABASE_PATH", "/data/calton.db")

    settings = Settings()

    assert settings.service.secret == "s3cret"
    assert settings.service.maxitemsperpage == 25
    assert settings.database.path == "/data/calton.db"


def test_secret_is_generated_when_unset() -> None:
    assert Settings().service.secret
    assert Settings().service.secret != Settings().service.secret


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
