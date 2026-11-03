"""Read-only facts for the System page, derived from the loaded config at request time.
Key variables are reported as set or not set; a key value is never read into the page."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from ..config import UNCLEAR, Config
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
            "name": cls.name, "priority": cls.priority, "team": cls.team, "deadline": cls.deadline,
            "risk": cls.risk, "draft_mode": cls.draft_mode, "templates": len(ids),
            "languages": ", ".join(langs) or "-", "builtin": cls.name == UNCLEAR,
        })
    return rows


def rule_rows(config: Config) -> list[dict]:
    return [{"id": r.id, "action": r.action, "target": r.force_class, "keywords": len(r.patterns),
             "languages": ", ".join(r.languages) if r.languages else "all"} for r in config.rules]


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
