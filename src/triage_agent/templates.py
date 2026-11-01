"""Approved reply templates: Markdown files with YAML front-matter and {{field}} placeholders."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from .models import normalize_newlines

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class Template(BaseModel):
    id: str
    class_name: str
    lang: str
    version: str
    required_fields: list[str]
    synthetic: bool
    body: str
    path: Path

    def placeholders(self) -> set[str]:
        return set(PLACEHOLDER.findall(self.body))


class TemplateFormatError(Exception):
    pass


def parse_template(path: Path) -> Template:
    text = normalize_newlines(path.read_text(encoding="utf-8"))
    if not text.startswith("---\n"):
        raise TemplateFormatError(f"{path}: missing front-matter")
    try:
        _, front, body = text.split("---\n", 2)
        meta = yaml.safe_load(front) or {}
        return Template(
            id=meta["id"],
            class_name=meta["class"],
            lang=meta["lang"],
            version=str(meta["version"]),
            required_fields=list(meta.get("required_fields") or []),
            synthetic=bool(meta.get("synthetic", False)),
            body=body.strip() + "\n",
            path=path,
        )
    except (ValueError, KeyError, yaml.YAMLError) as exc:
        raise TemplateFormatError(f"{path}: bad front-matter ({exc!r})") from exc


class MissingField(Exception):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def fill(template: Template, metadata: dict[str, str], allowed_fields: set[str]) -> str:
    """Fill placeholders from ticket metadata only. Single pass, so values are never re-expanded."""
    for name in template.required_fields:
        if name not in allowed_fields or not metadata.get(name, "").strip():
            raise MissingField(name)

    def _value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in allowed_fields or name not in metadata:
            raise MissingField(name)
        return metadata[name]

    return PLACEHOLDER.sub(_value, template.body)
