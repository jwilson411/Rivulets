from pathlib import Path

from fastapi.testclient import TestClient

from rivulets.api.health import _dir_size_mb, _file_size_mb  # pyright: ignore[reportPrivateUsage]


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


def test_file_size_mb_is_zero_for_a_nonexistent_path(tmp_path: Path) -> None:
    assert _file_size_mb(tmp_path / "does-not-exist.db") == 0.0


def test_dir_size_mb_is_zero_for_a_nonexistent_path(tmp_path: Path) -> None:
    assert _dir_size_mb(tmp_path / "does-not-exist") == 0.0


def test_file_size_mb_reflects_real_file_size(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"x" * (1024 * 1024))  # exactly 1 MB
    assert _file_size_mb(path) == 1.0


def test_dir_size_mb_sums_files_recursively(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * (512 * 1024))
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * (512 * 1024))
    assert _dir_size_mb(tmp_path) == 1.0
