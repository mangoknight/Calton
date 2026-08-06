from fastapi.testclient import TestClient

from calton import __version__
from calton.config import Settings
from calton.main import create_app


def test_health_returns_200() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
