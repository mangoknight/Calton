"""Shared test fixtures.

The settings model reads the ambient process environment, so a developer (or a CI
runner) who happens to export ``CALTON_SERVICE_SECRET`` would silently change what the
configuration tests assert. Every test therefore starts from a clean ``CALTON_*``
environment and opts in explicitly with ``monkeypatch.setenv``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from calton.config import get_settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import os

    for name in list(os.environ):
        if name.startswith("CALTON_"):
            monkeypatch.delenv(name, raising=False)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
