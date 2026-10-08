"""Deployment files must not expose the app beyond the host's loopback or Docker bridge."""

import re
import shlex
from fnmatch import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.edge.yml")


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def ports(name: str) -> list[str]:
    return [str(port) for port in load(name)["services"]["app"].get("ports", [])]


def test_compose_publishes_only_on_loopback():
    published = ports("docker-compose.yml")
    assert published == ["127.0.0.1:8000:8000"]


def test_edge_override_adds_only_the_docker_bridge_address():
    published = ports("docker-compose.edge.yml")
    assert published
    assert all(port.startswith("172.17.0.1:") for port in published)


def test_no_compose_file_binds_every_interface():
    for name in COMPOSE_FILES:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "0.0.0.0" not in text, name
        for port in ports(name):
            # A bare "8000:8000" publishes on every interface.
            assert re.match(r"^\d+\.\d+\.\d+\.\d+:", port), f"{name}: {port}"


def test_compose_keeps_data_and_secrets_outside_the_image():
    app = load("docker-compose.yml")["services"]["app"]
    assert app["env_file"] == ".env"
    assert "./data:/app/data" in app["volumes"]
    assert app["restart"] == "unless-stopped"


def test_dockerignore_excludes_secrets_and_data():
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert ".env" in lines
    assert ".env.*" in lines
    assert "data/" in lines


def test_dockerfile_runs_as_a_non_root_user_that_owns_the_data_folder():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    users = re.findall(r"^USER\s+(\S+)", text, flags=re.MULTILINE)
    assert users, "Dockerfile has no USER"
    assert users[-1] not in {"root", "0"}
    assert "chown 10001:10001 /app/data" in text
    assert "--no-proxy-headers" in text


def test_deploy_script_serialises_runs_and_checks_the_deployed_commit():
    text = (ROOT / "deploy" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "flock -n 9" in text
    assert "git reset --hard origin/main" in text
    assert "git_sha" in text


def test_caddy_example_sets_hsts_and_an_access_log():
    text = (ROOT / "deploy" / "Caddyfile.example").read_text(encoding="utf-8")
    assert "Strict-Transport-Security" in text
    assert "roll_size 50MiB" in text
    assert "roll_keep 10" in text
    assert "0.0.0.0" not in text


def test_workflows_are_valid_yaml_with_least_privilege():
    for name in ("ci.yml", "deploy.yml"):
        workflow = load(f".github/workflows/{name}")
        assert workflow["permissions"] == {"contents": "read"}, name
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "StrictHostKeyChecking" not in deploy


def dockerignore_patterns() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def is_ignored(path: str) -> bool:
    """Whether .dockerignore keeps `path` (or one of its parent folders) out of the build context."""
    parts = path.strip("/").split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    ignored = False
    for pattern in dockerignore_patterns():
        negated = pattern.startswith("!")
        glob = pattern.lstrip("!").strip("/")
        if any(fnmatch(candidate, glob) for candidate in candidates):
            ignored = not negated
    return ignored


def dockerfile_copy_sources() -> list[str]:
    sources = []
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        words = shlex.split(line)[1:]
        if any(word.startswith("--from=") for word in words):
            continue  # copied from another build stage, not from the context
        sources += [word for word in words[:-1] if not word.startswith("--")]
    return sources


def test_dockerfile_copy_sources_exist_and_are_not_ignored():
    sources = dockerfile_copy_sources()
    assert "app/" in sources and "VERSION" in sources and "frontend/package-lock.json" in sources
    for source in sources:
        assert (ROOT / source).exists(), f"COPY source missing: {source}"
        assert not is_ignored(source), f"COPY source excluded by .dockerignore: {source}"


def test_dockerignore_keeps_deploy_and_agent_files_out_of_the_image():
    for name in ("deploy/", ".git"):
        assert name in dockerignore_patterns()
        assert is_ignored(name)
