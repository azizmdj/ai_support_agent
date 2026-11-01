"""Load and validate a pack (taxonomy.yaml, rules.yaml, settings.yaml, templates/)."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .models import normalize_newlines
from .templates import Template, TemplateFormatError, parse_template

UNCLEAR = "unclear"
PRIORITY_RANK = {"urgent": 3, "high": 2, "normal": 1, "low": 0}

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_duration(value: str) -> timedelta:
    """ISO 8601 duration limited to days/hours/minutes (calendar time, no months or years)."""
    match = _DURATION.match(value or "")
    if not match or not any(match.groups()) or value.endswith("T"):
        raise ValueError(f"bad duration {value!r}")
    days, hours, minutes = (int(g or 0) for g in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes)


class TemplateRef(BaseModel):
    id: str
    when: str | None = None  # subtype this template answers
    default: bool = False


class ClassConfig(BaseModel):
    name: str
    description: str
    priority: Literal["urgent", "high", "normal", "low"]
    team: str
    deadline: str
    risk: Literal["locked", "normal"]
    draft_mode: Literal["template", "none"]
    subtypes: list[str] = Field(default_factory=list)
    templates: list[TemplateRef] = Field(default_factory=list)

    @property
    def locked(self) -> bool:
        return self.risk == "locked"

    @property
    def deadline_delta(self) -> timedelta:
        return parse_duration(self.deadline)

    @property
    def rank(self) -> int:
        return PRIORITY_RANK[self.priority]


class Rule(BaseModel):
    id: str
    patterns: list[str]  # keyword (word-start match) or "re:<regex>" on normalized text
    languages: list[str] | None = None
    action: Literal["escalate", "route"]
    force_class: str


class Settings(BaseModel):
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    allowed_fields: list[str]
    ai_disclosure: dict[str, str]
    unclear_deadline: str = "PT24H"


class Config(BaseModel):
    classes: dict[str, ClassConfig]
    rules: list[Rule]
    settings: Settings
    templates: dict[tuple[str, str], Template]  # (template_id, lang) -> Template
    version: str

    def template(self, template_id: str, lang: str) -> Template | None:
        return self.templates.get((template_id, lang))


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("invalid config:\n- " + "\n- ".join(errors))
        self.errors = errors


def config_version(pack_dir: Path) -> str:
    """Hash of every pack file (path + LF-normalized content)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in pack_dir.rglob("*") if p.is_file()):
        digest.update(path.relative_to(pack_dir).as_posix().encode("utf-8") + b"\0")
        digest.update(normalize_newlines(path.read_text(encoding="utf-8")).encode("utf-8") + b"\0")
    return digest.hexdigest()[:12]


def _unclear_class(settings: Settings) -> ClassConfig:
    return ClassConfig(
        name=UNCLEAR,
        description="Built-in: the message could not be classified with enough confidence.",
        priority="normal",
        team="human_queue",
        deadline=settings.unclear_deadline,
        risk="normal",
        draft_mode="none",
    )


def _read_yaml(path: Path) -> object:
    return yaml.safe_load(normalize_newlines(path.read_text(encoding="utf-8")))


def load_config(pack_dir: Path) -> Config:
    pack_dir = Path(pack_dir)
    errors: list[str] = []
    try:
        raw_classes = _read_yaml(pack_dir / "taxonomy.yaml")["classes"]
        raw_rules = _read_yaml(pack_dir / "rules.yaml")["rules"]
        settings = Settings.model_validate(_read_yaml(pack_dir / "settings.yaml"))
        class_list = [ClassConfig.model_validate(c) for c in raw_classes]
        rules = [Rule.model_validate(r) for r in raw_rules]
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError([f"cannot parse pack: {exc}"]) from exc

    classes: dict[str, ClassConfig] = {}
    for cls in class_list:
        if cls.name in classes or cls.name == UNCLEAR:
            errors.append(f"duplicate class name {cls.name!r}")
        classes[cls.name] = cls
    try:
        classes[UNCLEAR] = _unclear_class(settings)
    except ValidationError as exc:
        errors.append(f"bad unclear settings: {exc}")

    for cls in classes.values():
        try:
            parse_duration(cls.deadline)
        except ValueError as exc:
            errors.append(f"class {cls.name}: {exc}")
        if cls.locked and cls.draft_mode != "none":
            errors.append(f"class {cls.name} is locked but draft_mode is {cls.draft_mode!r}")
        if cls.draft_mode == "template":
            if not cls.templates:
                errors.append(f"class {cls.name} drafts but lists no templates")
            elif sum(t.default for t in cls.templates) != 1:
                errors.append(f"class {cls.name} needs exactly one default template")
            for ref in cls.templates:
                if ref.when is not None and ref.when not in cls.subtypes:
                    errors.append(f"class {cls.name}: template {ref.id} uses unknown subtype {ref.when!r}")

    rule_ids: set[str] = set()
    for rule in rules:
        if rule.id in rule_ids:
            errors.append(f"duplicate rule id {rule.id!r}")
        rule_ids.add(rule.id)
        target = classes.get(rule.force_class)
        if target is None or rule.force_class == UNCLEAR:
            errors.append(f"rule {rule.id}: unknown class {rule.force_class!r}")
        elif target.locked and rule.action != "escalate":
            errors.append(f"rule {rule.id}: locked class {target.name} only allows action 'escalate'")
        for pattern in rule.patterns:
            if pattern.startswith("re:"):
                try:
                    re.compile(pattern[3:])
                except re.error as exc:
                    errors.append(f"rule {rule.id}: bad regex {pattern!r}: {exc}")

    # Templates: every referenced id needs a file; every file must belong to a declared class/id.
    referenced = {ref.id: cls.name for cls in classes.values() for ref in cls.templates}
    allowed = set(settings.allowed_fields)
    templates: dict[tuple[str, str], Template] = {}
    for path in sorted((pack_dir / "templates").glob("*/*.md")):
        try:
            tpl = parse_template(path)
        except TemplateFormatError as exc:
            errors.append(str(exc))
            continue
        where = path.relative_to(pack_dir).as_posix()
        if tpl.id != path.stem or tpl.lang != path.parent.name:
            errors.append(f"{where}: front-matter id/lang does not match file location")
        if tpl.class_name not in classes:
            errors.append(f"{where}: unknown class {tpl.class_name!r}")
        if tpl.id not in referenced:
            errors.append(f"{where}: unknown template id {tpl.id!r} (not in taxonomy)")
        elif referenced[tpl.id] != tpl.class_name:
            errors.append(f"{where}: class {tpl.class_name!r} does not match taxonomy {referenced[tpl.id]!r}")
        if not tpl.synthetic:
            errors.append(f"{where}: must be marked synthetic: true")
        if not set(tpl.required_fields) <= allowed:
            errors.append(f"{where}: required_fields not in allowed_fields: {sorted(set(tpl.required_fields) - allowed)}")
        if not tpl.placeholders() <= set(tpl.required_fields):
            errors.append(f"{where}: placeholders not declared in required_fields")
        if tpl.lang not in settings.ai_disclosure:
            errors.append(f"{where}: no ai_disclosure line for language {tpl.lang!r}")
        templates[(tpl.id, tpl.lang)] = tpl
    present_ids = {tid for tid, _ in templates}
    for tid, cname in referenced.items():
        if tid not in present_ids:
            errors.append(f"class {cname}: missing template file for {tid!r}")

    if errors:
        raise ConfigError(errors)
    return Config(
        classes=classes,
        rules=rules,
        settings=settings,
        templates=templates,
        version=config_version(pack_dir),
    )
