"""Read-only facts for the System page, derived from the loaded config at request time.
Key variables are reported as set or not set; a key value is never read into the page."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from ..config import UNCLEAR, Config, parse_duration
from ..llm import build_system_prompt, build_user_prompt
from ..models import ALLOWED_TRANSITIONS
from ..providers.registry import effective_params


def provider_rows(config: Config, chain: list[str], env: Mapping[str, str]) -> list[dict]:
    rows = []
    for pid in chain:
        spec = config.models.providers.get(pid)
        if spec is None:  # an injected test double
            rows.append({"id": pid, "kind": "injected", "model": "-", "host": "-", "temperature": "-",
                         "max_tokens": "-", "timeout": "-", "key": "-"})
            continue
        params = effective_params(spec)
        key = "-"
        if spec.api_key_env:
            key = f"{spec.api_key_env}: {'set' if env.get(spec.api_key_env, '').strip() else 'not set'}"
        rows.append({
            "id": pid,
            "kind": spec.kind,
            "model": spec.model or "keyword fake (offline)",
            "host": urlparse(spec.base_url).netloc if spec.base_url else "-",
            "temperature": "none sent" if params["temperature"] is None else params["temperature"],
            "max_tokens": params["max_tokens"] or "-",
            "timeout": f"{params['timeout_seconds']:g} s" if params["timeout_seconds"] else "-",
            "key": key,
        })
    return rows


TEST_MODEL = "test model (no real AI)"


def human_duration(value: str) -> str:
    """ "PT1H" -> "1 hour", "P15D" -> "15 days", "PT24H" -> "1 day". Text that is not a duration is shown as is."""
    try:
        delta = parse_duration(value)
    except ValueError:
        return value
    minutes = int(delta.total_seconds()) // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = [f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in ((days, "day"), (hours, "hour"), (mins, "minute")) if n]
    return " ".join(parts) or value


RISK_LABELS = {"locked": "locked: always a person", "normal": "normal"}
REPLY_LABELS = {"template": "approved template", "none": "no draft: a person replies"}


def provider_label(config: Config, pid: str) -> str:
    """Plain name for the header and sidebar: the offline fake is called what it is."""
    spec = config.models.providers.get(pid)
    if (spec is not None and spec.kind == "fake") or pid == "FakeLLM":  # FakeLLM: an injected test double
        return TEST_MODEL
    return pid


def model_summary(config: Config, pid: str) -> str:
    """Short config text for one provider, used in the trail's config column."""
    spec = config.models.providers.get(pid)
    if spec is None:
        return pid
    if spec.kind == "fake":
        return f"{pid}: keyword fake (offline)"
    params = effective_params(spec)
    temp = "no temperature" if params["temperature"] is None else f"temperature {params['temperature']}"
    return f"{pid}: model {spec.model}, {temp}"


def class_rows(config: Config) -> list[dict]:
    rows = []
    for cls in config.classes.values():
        ids = {ref.id for ref in cls.templates}
        langs = sorted({lang for (tid, lang) in config.templates if tid in ids})
        rows.append({
            "name": cls.name, "priority": cls.priority, "team": cls.team, "deadline": human_duration(cls.deadline),
            "risk": RISK_LABELS.get(cls.risk, cls.risk), "draft_mode": REPLY_LABELS.get(cls.draft_mode, cls.draft_mode),
            "templates": len(ids), "description": cls.description, "locked": cls.locked,
            "languages": ", ".join(langs) or "-", "builtin": cls.name == UNCLEAR,
        })
    return rows


def rule_rows(config: Config) -> list[dict]:
    return [{"id": r.id, "action": r.action, "target": r.force_class, "keywords": len(r.patterns),
             "patterns": r.patterns, "languages": ", ".join(r.languages) if r.languages else "all"}
            for r in config.rules]


def template_rows(config: Config) -> list[dict]:
    """Every approved template file, in taxonomy order, with its text."""
    order = {name: i for i, name in enumerate(config.classes)}
    rows = sorted(config.templates.values(), key=lambda t: (order.get(t.class_name, 99), t.id, t.lang))
    return [{"id": t.id, "class": t.class_name, "lang": t.lang, "fields": ", ".join(t.required_fields) or "-",
             "body": t.body.strip("\n"), "file": f"pack/templates/{t.lang}/{t.id}.md"} for t in rows]


PROMPT_PLACEHOLDER = "(the masked customer message goes here)"


def classifier_prompt(config: Config) -> tuple[str, str]:
    """The exact system and user prompt a real model receives, built by the same functions the classifier uses.
    The message is a placeholder; nothing is sent anywhere."""
    classes = [c for c in config.classes.values() if c.name != UNCLEAR]  # as in Pipeline._classify
    return build_system_prompt(classes), build_user_prompt(PROMPT_PLACEHOLDER)


def flow_rows(config: Config, chain: list[str]) -> list[dict]:
    langs = ", ".join(sorted(config.settings.ai_disclosure))
    return [
        {"anchor": "received", "step": "1. Message received", "who": "Customer",
         "what": "A message arrives with structured fields such as name and card ending.",
         "io": "ticket -> case (state new)", "controlled": "TicketSource adapter; pack/scenarios.yaml for test messages"},
        {"anchor": "rules", "step": "2. Rules", "who": "Code",
         "what": "Keyword and regex rules send fraud and complaints straight to a person, with no model call.",
         "io": "message text -> rule id or no match", "controlled": f"pack/rules.yaml ({len(config.rules)} rules)"},
        {"anchor": "classify", "step": "3. Classify", "who": "AI",
         "what": "The model labels the masked message with a class, sub-type and confidence.",
         "io": "masked message -> class, sub-type, confidence, provider",
         "controlled": f"pack/models.yaml (chain {' -> '.join(chain)}); confidence_threshold "
                       f"{config.settings.confidence_threshold} in pack/settings.yaml"},
        {"anchor": "template", "step": "4. Template", "who": "Code",
         "what": "The approved template for the class is filled from ticket fields only.",
         "io": "class, language, ticket fields -> draft or no draft",
         "controlled": f"pack/taxonomy.yaml, pack/templates/ ({len(config.templates)} files), allowed fields"},
        {"anchor": "review", "step": "5. Review", "who": "Reviewer",
         "what": "A person approves, edits, escalates, corrects the class or takes the case.",
         "io": "draft or case -> approved, escalated or taken", "controlled": "the approval gate (gate.py)"},
        {"anchor": "reply", "step": "6. Reply", "who": "Code",
         "what": "Only an approved draft is sent; the customer receives the reply, with an AI-assistance note appended by code.",
         "io": "approved draft -> reply via the ticket system",
         "controlled": f"ai_disclosure in pack/settings.yaml ({langs})"},
    ]


def transitions() -> list[tuple[str, str]]:
    return [(src.value, ", ".join(sorted(t.value for t in targets))) for src, targets in ALLOWED_TRANSITIONS.items()]
