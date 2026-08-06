"""Application settings.

Key names are kept identical to the upstream Go configuration (``pkg/config/config.go``)
so that an existing Calton ``config.yml`` or ``CALTON_*`` environment is understood
unchanged: ``service.secret`` maps to ``CALTON_SERVICE_SECRET``, ``database.path`` to
``CALTON_DATABASE_PATH`` and so on. Upstream key names are lowercase without separators
(``maxitemsperpage``, ``jwtttlshort``), which is what makes a single ``_`` usable as the
section delimiter.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseModel):
    """``service.*`` — defaults mirror ``initDefaultConfig()`` unless noted."""

    secret: str = ""
    jwtttl: int = 259200  # 72h
    jwtttllong: int = 2592000  # 30d
    jwtttlshort: int = 600  # 10m — the TTL Calton issues access tokens with
    interface: str = ":3456"
    publicurl: str = ""
    rootpath: str = "."
    maxitemsperpage: int = 50
    motd: str = ""
    timezone: str = "GMT"
    bcryptrounds: int = 11
    # ⚠️ Enables the table-rewriting /test/* routes when non-empty. The parity
    # harness sets it; a deployment must never. See api/v1/testing.py.
    testingtoken: str = ""
    demomode: bool = False
    enableregistration: bool = True
    enablelinksharing: bool = True
    enabletaskattachments: bool = True
    enabletaskcomments: bool = True
    # Capabilities Calton does not implement. Upstream defaults these to true; reporting
    # them honestly through /info is the agreed degradation path (design §5.3).
    enablecaldav: bool = False
    enabletotp: bool = False

    @model_validator(mode="after")
    def _ensure_secret(self) -> ServiceSettings:
        if not self.secret:
            # Upstream generates a random secret when none is configured; doing the same
            # keeps a zero-config start working, at the cost of invalidating tokens on
            # restart. Deployments are expected to set service.secret.
            self.secret = secrets.token_hex(32)
        return self


class WebhooksSettings(BaseModel):
    """``webhooks.*``. Its own section upstream, not part of ``service``.

    ⚠️ **Default true, matching ``config.go:489``** — and that default is load-bearing
    rather than cosmetic. Upstream does not mount the four project-webhook routes when
    this is false, and it reports the flag through ``/info.webhooks_enabled``; both were
    measured on both settings. The parity harness currently runs the Go side with
    ``CALTON_WEBHOOKS_ENABLED=false`` while the MCP gate runs it on upstream's defaults,
    so the same endpoint is 201 under one device and "does not exist" under the other.

    Reading the flag here is what lets Calton be right on **both** planes at once. Any
    constant — true or false — is a divergence on one of them, and the harness compares
    ``/info`` on every case, so picking one would mean a known-broken window until the
    two devices are aligned.
    """

    enabled: bool = True


class DatabaseSettings(BaseModel):
    """``database.*``. SQLite is the default; MySQL is supported for deployments.

    The key names match upstream Vikunja's ``database`` section so an existing
    ``CALTON_DATABASE_*`` environment is understood unchanged: ``type``, ``host``,
    ``user``, ``password``, ``database``, and ``path`` (sqlite only).
    """

    type: Literal["sqlite", "mysql"] = "sqlite"
    #: sqlite only — the database file.
    path: str = "calton.db"
    #: mysql only.
    host: str = "localhost"
    port: int = 3306
    user: str = "calton"
    password: str = ""
    #: mysql schema name (upstream calls this key ``database``).
    database: str = "calton"


class FilesSettings(BaseModel):
    """``files.*``."""

    basepath: str = "files"
    maxsize: str = "20MB"
    type: Literal["local"] = "local"


class LogSettings(BaseModel):
    """``log.*`` — only the subset Calton acts on."""

    enabled: bool = True
    level: str = "INFO"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALTON_",
        env_nested_delimiter="_",
        extra="ignore",
    )

    service: ServiceSettings = Field(default_factory=ServiceSettings)
    webhooks: WebhooksSettings = Field(default_factory=WebhooksSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    files: FilesSettings = Field(default_factory=FilesSettings)
    log: LogSettings = Field(default_factory=LogSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
