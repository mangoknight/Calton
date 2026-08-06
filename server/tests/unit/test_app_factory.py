"""``calton.main`` must be importable without doing anything."""

import subprocess
import sys
from pathlib import Path

IMPORT_PROBE = """
import sys
from calton.config import get_settings

before = set(sys.modules)
import calton.main

assert get_settings.cache_info().currsize == 0, "importing main read settings"
assert not any(
    isinstance(getattr(calton.main, name), __import__("fastapi").FastAPI)
    for name in dir(calton.main)
), "importing main built an app instance"
assert callable(calton.main.create_app)
"""


def test_importing_main_has_no_side_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    # A stray settings read or engine creation would litter the working directory.
    assert list(tmp_path.iterdir()) == []


def test_create_app_registers_health_route() -> None:
    from calton.config import Settings
    from calton.main import create_app

    routes = {getattr(route, "path", None) for route in create_app(Settings()).routes}
    assert "/health" in routes
