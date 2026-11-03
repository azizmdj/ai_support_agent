from conftest import edit_yaml
from fastapi.testclient import TestClient

from triage_agent.web.app import create_app

FAKE_KEY = "test-key-not-real-0007"


def system_html(app):
    return TestClient(app).get("/system").text


def test_system_page_shows_flow_settings_and_business_rules(pack):
    app = create_app(pack=pack, db_path=":memory:")
    html = system_html(app)
    for anchor in ("rules", "classify", "template", "review", "reply", "settings"):
        assert f'id="{anchor}"' in html
    assert "<th>confidence_threshold</th><td>0.7</td>" in html
    assert f"<code>{app.state.config.version}</code>" in html
    for name in ("fraud_report", "complaint", "card_blocked", "unclear (built in)"):
        assert f"<td>{name}</td>" in html
    assert "<td>fraud_keywords</td><td>escalate</td><td>fraud_report</td>" in html
    assert "Fraud and complaint classes are locked" in html
    assert html.count("<details>") == 3 and "<details open" not in html  # Explain sections start closed
    assert "<form" not in html.split("</header>", 1)[1]  # read-only: no forms below the header


def test_system_page_follows_the_pack_after_a_restart(pack):
    before = create_app(pack=pack, db_path=":memory:", seed_tickets=None)
    edit_yaml(pack / "settings.yaml", lambda d: d.update(confidence_threshold=0.85))
    edit_yaml(pack / "taxonomy.yaml", lambda d: d["classes"].append({
        "name": "tax_question", "description": "Questions about taxes.", "priority": "low", "team": "general_support",
        "deadline": "P3D", "risk": "normal", "draft_mode": "none"}))
    after = create_app(pack=pack, db_path=":memory:", seed_tickets=None)
    html = system_html(after)
    assert "<th>confidence_threshold</th><td>0.85</td>" in html and "<td>tax_question</td>" in html
    assert before.state.config.version != after.state.config.version
    assert f"<code>{after.state.config.version}</code>" in html


def test_system_page_shows_key_status_but_never_a_key(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "openai_main,fake")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    app = create_app(db_path=":memory:", seed_tickets=None)
    html = system_html(app)
    assert "OPENAI_API_KEY: set" in html
    row = ("<td>openai_main</td><td>openai_compatible</td><td>gpt-4o-mini</td><td>api.openai.com</td>"
           "<td>0.0</td><td>300</td><td>20 s</td>")
    assert row in html
    assert "Model: openai_main, then fake" in html
    for page in ("/system", "/", "/health"):
        assert FAKE_KEY not in TestClient(app).get(page).text
    from triage_agent.web.system import provider_rows

    assert provider_rows(app.state.config, ["openai_main"], {})[0]["key"] == "OPENAI_API_KEY: not set"


def test_system_get_changes_nothing_and_escapes_config_text(pack):
    edit_yaml(pack / "taxonomy.yaml", lambda d: d["classes"][0].update(team="<script>alert(3)</script>"))
    app = create_app(pack=pack, db_path=":memory:")
    before = app.state.store.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    html = system_html(app)
    assert "<script>alert(3)</script>" not in html and "&lt;script&gt;alert(3)&lt;/script&gt;" in html
    assert app.state.store.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == before
    assert TestClient(app).post("/system").status_code == 405
