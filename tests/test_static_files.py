"""The client catch-all must only ever serve files from inside the static directory."""

from __future__ import annotations

import pytest

SECRET = "not-for-the-browser"


@pytest.fixture
def site(make_client, tmp_path):
    root = tmp_path / "root"
    (root / "static" / "assets").mkdir(parents=True)
    (root / "static" / "index.html").write_text("<!doctype html>index", encoding="utf-8")
    (root / "static" / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "secret.txt").write_text(SECRET, encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "app.db").write_text(SECRET, encoding="utf-8")
    return make_client(static_dir=root / "static")


def test_serves_a_built_asset(site):
    response = site.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


def test_unknown_route_falls_back_to_index(site):
    response = site.get("/lessons/some-lesson")
    assert response.status_code == 200
    assert response.text == "<!doctype html>index"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/secret.txt",
        "/%2E%2E/secret.txt",
        "/..%2fsecret.txt",
        "/%2e%2e%2fsecret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/%2e%2e/data/app.db",
        "/..%5csecret.txt",
    ],
)
def test_never_serves_a_file_outside_static(site, path):
    response = site.get(path)
    assert SECRET not in response.text


def test_odd_paths_fall_back_to_index(site):
    for path in ("/%00", "/assets/%00app.js", "//etc/passwd"):
        response = site.get(path)
        assert response.status_code == 200, path
        assert response.text == "<!doctype html>index"


def test_missing_client_build_is_a_json_404(client):
    response = client.get("/")
    assert response.status_code == 404
    assert "detail" in response.json()
