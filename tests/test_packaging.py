import yaml
from fastapi.testclient import TestClient

from triage_agent import REPO_ROOT
from triage_agent.web.app import create_app

KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def audit_count(app):
    return app.state.store.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]


# --- Health endpoint -------------------------------------------------------------------------------
def test_health_reports_ok_and_provider_ids():
    app = create_app(db_path=":memory:")
    client = TestClient(app)
    before = audit_count(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "models": ["fake"]}
    assert audit_count(app) == before
    assert client.post("/health").status_code == 405


def test_health_shows_chain_but_never_a_key(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "openai_main,fake")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real-0006")
    response = TestClient(create_app(db_path=":memory:", seed_tickets=None)).get("/health")
    assert response.json() == {"status": "ok", "models": ["openai_main", "fake"]}
    assert "test-key-not-real-0006" not in response.text


def test_serve_has_host_option(monkeypatch):
    import triage_agent.__main__ as cli

    seen = {}
    monkeypatch.setattr(cli, "serve", lambda pack, host, port: seen.update(host=host, port=port) or 0)
    assert cli.main(["serve"]) == 0 and seen == {"host": "127.0.0.1", "port": 8000}
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"]) == 0
    assert seen == {"host": "0.0.0.0", "port": 9000}


# --- Compose, Dockerfile, CI ---------------------------------------------------------------------
def compose():
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_passes_keys_by_name_only():
    service = compose()["services"]["console"]
    env = service["environment"]
    assert isinstance(env, list)
    for name in KEY_VARS:
        assert name in env  # bare name: compose passes the host value through
        assert not any(item.startswith(name + "=") for item in env)
    assert "TRIAGE_DB=/data/triage.db" in env
    assert "TRIAGE_PROVIDER=${TRIAGE_PROVIDER:-fake}" in env
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in KEY_VARS:
        assert f"{name}=" not in text and f"{name}:" not in text


def test_compose_binds_localhost_and_uses_a_named_volume():
    data = compose()
    service = data["services"]["console"]
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert "triage-data:/data" in service["volumes"]
    assert "triage-data" in data["volumes"]
    assert "# - ./pack:/app/pack:ro" in (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_dockerfile_basics():
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert text.startswith("#") and "FROM python:3.13-slim" in text
    assert text.index("COPY pyproject.toml uv.lock") < text.index("COPY src/")  # layer caching
    assert "uv sync --frozen --no-dev" in text
    assert "USER app" in text and "EXPOSE 8000" in text
    assert "HEALTHCHECK" in text and "urllib.request" in text and "curl" not in text
    assert '"--host", "0.0.0.0"' in text
    # Every path the image copies exists in the repo.
    for line in text.splitlines():
        if line.startswith("COPY ") and "--from" not in line:
            for src in line.split()[1:-1]:
                assert (REPO_ROOT / src).exists(), src


def test_dockerignore_keeps_secrets_and_local_state_out():
    lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".git", ".venv", "__pycache__", ".pytest_cache", ".env", "*.env", "*.db"):
        assert entry in lines, entry


def test_image_includes_the_eval_reports_for_the_monitor_tab():
    # The Monitor tab reads eval/results/; the reports hold metrics and ticket ids only, no message text or keys.
    lines = [line.strip() for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()]
    assert not any(line.rstrip("/") in {"eval", "eval/results", "eval/results/*", "*.md"} for line in lines)
    assert "COPY eval/ eval/" in (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert list((REPO_ROOT / "eval" / "results").glob("*.md"))  # there is a report to show


def test_ci_runs_lint_and_tests_without_secrets():
    data = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    on = data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` key as True
    assert on["push"]["branches"] == ["main"] and "pull_request" in on
    steps = data["jobs"]["test"]["steps"]
    runs = [s.get("run") for s in steps if "run" in s]
    assert runs == ["uv sync --frozen", "uv run ruff check .", "uv run pytest"]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses[0].startswith("actions/checkout@v") and uses[1].startswith("astral-sh/setup-uv@v")
    assert "secrets." not in (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
