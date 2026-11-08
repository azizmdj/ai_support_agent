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
    assert "<th>Confidence needed for a draft</th><td>0.7</td>" in html
    assert f"<code>{app.state.config.version}</code>" in html
    for name in ("fraud_report", "complaint", "card_blocked", "unclear (built in)"):
        assert f"<td>{name}</td>" in html
    assert "<td>fraud_keywords</td><td>fraud_report</td><td>all</td><td><code>fraud</code>, <code>scam</code>" in html
    assert "Fraud and complaint classes are locked" in html
    assert "<td>1 hour</td>" in html and "<td>15 days</td>" in html and "PT1H" not in html  # durations in words
    assert "locked: always a person" in html and "draft_mode" not in html and "config_version" not in html
    assert html.count("<details>") == 3  # the three Explain sections, closed
    assert html.count(" open>") == 1 and '<details class="block" id="how" open>' in html  # only "How it works"
    assert "<form" not in html.split("</header>", 1)[1]  # read-only: no forms below the header
    assert 'class="sidebar"' not in html and "Pause: <strong>OFF</strong>" in html  # status as text, no switch


def test_system_page_follows_the_pack_after_a_restart(pack):
    before = create_app(pack=pack, db_path=":memory:", seed_tickets=None)
    edit_yaml(pack / "settings.yaml", lambda d: d.update(confidence_threshold=0.85))
    edit_yaml(pack / "taxonomy.yaml", lambda d: d["classes"].append({
        "name": "tax_question", "description": "Questions about taxes.", "priority": "low", "team": "general_support",
        "deadline": "P3D", "risk": "normal", "draft_mode": "none"}))
    after = create_app(pack=pack, db_path=":memory:", seed_tickets=None)
    html = system_html(after)
    assert "<th>Confidence needed for a draft</th><td>0.85</td>" in html and "<td>tax_question</td>" in html
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
    assert "Model: openai_main, then test model (no real AI)" in html
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


def test_setup_page_shows_prompt_templates_fields_and_where_to_edit():
    from triage_agent.config import UNCLEAR
    from triage_agent.llm import build_system_prompt

    app = create_app(db_path=":memory:", seed_tickets=None)
    html = system_html(app)
    config = app.state.config
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert 'aria-current="page">Setup</a>' in header
    assert "<h1>Setup</h1>" in html and "<strong>Rules first.</strong>" in html
    assert "<strong>The AI only classifies.</strong>" in html and "<strong>A person approves.</strong>" in html
    # The exact prompt, from the same function the classifier uses, with a placeholder for the message.
    expected = build_system_prompt([c for c in config.classes.values() if c.name != UNCLEAR])
    assert html_unescape(html.split('<pre class="prompt">', 1)[1].split("</pre>", 1)[0]) == expected
    assert "(the masked customer message goes here)" in html
    # Descriptions, every template body, the allowed fields and the AI-assistance lines.
    for cls in config.classes.values():
        assert html_escape(cls.description) in html
    assert html.count('<details class="tpl">') == len(config.templates) == 12
    assert html_escape(config.templates[("fee_question_reply", "en")].body.strip().splitlines()[0]) in html
    for field in config.settings.allowed_fields:
        assert f"<code>{field}</code>" in html
    for line in config.settings.ai_disclosure.values():
        assert html_escape(line) in html
    assert "<th>fraud_report</th><td>1 hour</td>" in html
    for f in ("models.yaml", "taxonomy.yaml", "rules.yaml", "settings.yaml"):
        assert f"Change this in <code>pack/{f}</code>" in html
    assert "<form" not in html.split("</header>", 1)[1]


def html_escape(text):
    import html

    return html.escape(text, quote=True).replace("&#x27;", "&#39;")


def html_unescape(text):
    import html

    return html.unescape(text)
