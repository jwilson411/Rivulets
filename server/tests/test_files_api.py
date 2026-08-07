"""api/files.py's download/info routes -- test_message_attachments.py only
ever exercises upload + attach-to-message, never GET /files/{id} or
GET /files/{id}/info directly."""

from fastapi.testclient import TestClient


def _upload(
    client: TestClient, headers: dict[str, str], name: str, content: bytes
) -> dict[str, str]:
    response = client.post(
        "/api/v1/files/upload",
        files={"upload": (name, content, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_download_file_returns_the_uploaded_bytes(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    uploaded = _upload(client, auth_headers, "notes.txt", b"hello download")

    response = client.get(f"/api/v1/files/{uploaded['file_id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == b"hello download"
    assert response.headers["content-type"].startswith("text/plain")


def test_download_file_returns_404_for_unknown_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/files/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_file_info_returns_metadata_without_the_body(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    uploaded = _upload(client, auth_headers, "info.txt", b"metadata only")

    response = client.get(f"/api/v1/files/{uploaded['file_id']}/info", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "info.txt"
    assert body["size_bytes"] == len(b"metadata only")
    assert body["content_hash"] == uploaded["content_hash"]


def test_file_info_returns_404_for_unknown_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/files/does-not-exist/info", headers=auth_headers)
    assert response.status_code == 404
