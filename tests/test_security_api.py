"""Response headers, health and start-up checks of the running app."""

from __future__ import annotations

import pytest

from app.content import ContentError, load_program
from app.security import SECURITY_HEADERS
from app.settings import ROOT
from tests.conftest import FIXTURE_CONTENT
from tests.helpers import write_program


def _assert_security_headers(response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def test_security_headers_on_api_and_static(make_client, tmp_path):
    static = tmp_path / "site"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html>index", encoding="utf-8")
    (static / "app.js").write_text("console.log(1)", encoding="utf-8")
    client = make_client(static_dir=static)
    for response in (
        client.get("/api/health"),
        client.get("/api/config"),
        client.get("/api/account"),  # 401
        client.get("/api/nope"),  # 404
        client.post("/api/login", json={}),  # 422
        client.get("/app.js"),
        client.get("/some/client/route"),
    ):
        _assert_security_headers(response)


def test_index_is_never_cached(make_client, tmp_path):
    static = tmp_path / "site"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html>index", encoding="utf-8")
    client = make_client(static_dir=static)
    for path in ("/", "/index.html", "/lessons/first"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store", path


def test_health_has_no_user_count(signed_in):
    body = signed_in.get("/api/health").json()
    assert body == {
        "status": "ok",
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "git_sha": "dev",
        "program_version": load_program(FIXTURE_CONTENT).program_version,
        "chapters": 1,
        "lessons": 2,
        "cards": 4,
    }
    assert "users" not in body


def test_config_describes_the_course(make_client):
    assert make_client().get("/api/config").json() == {
        "title": "Fixture course",
        "description": "A tiny course used by the test suite.",
        "language": "en",
        "registration_open": False,
    }
    assert make_client(registration_open=True).get("/api/config").json()["registration_open"] is True


def test_unknown_api_path_is_json_404(client):
    for method, path in (
        ("get", "/api/nope"),
        ("post", "/api/nope"),
        ("get", "/api"),
        ("delete", "/api/chapters"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"detail": "not found"}


def test_validation_errors_are_a_single_message(client):
    response = client.post("/api/login", json={"username": "learner"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "password" in detail


def test_api_docs_are_not_published(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert "swagger" not in response.text.lower()
        assert "openapi" not in response.text.lower()


def test_app_refuses_to_start_on_invalid_content(make_client, tmp_path):
    content = write_program(tmp_path / "course")
    chapter = content / "chapters" / "basics.yaml"
    chapter.write_text(
        chapter.read_text(encoding="utf-8").replace("id: largest-planet", "id: capital-of-france")
    )
    with pytest.raises(ContentError) as error:
        make_client(content_dir=content)
    assert any("duplicate card id" in message for message in error.value.errors)
