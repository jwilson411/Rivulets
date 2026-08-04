from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "db_size_mb" in body["resources"]


def test_info(client: TestClient) -> None:
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    assert response.json()["version"] == "0.1.0"
