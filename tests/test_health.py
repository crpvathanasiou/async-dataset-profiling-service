from fastapi.testclient import TestClient

from app.main import app


def test_app_starts() -> None:
    assert app.title == "async-dataset-profiling-service"


def test_health_live_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "app_env" in data
    assert "app_version" in data


def test_health_ready_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "app_env" in data
    assert "app_version" in data
