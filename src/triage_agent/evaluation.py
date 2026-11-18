"""Evaluation suite: runs the synthetic ticket set through the real pipeline and measures accuracy,
safety and privacy. Offline by default (fake provider behind the real LLMClassifier, so masking
really runs); the same code measures a live provider chain. Nothing is ever approved or sent.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import REPO_ROOT
from .audit import AuditLog
from .config import UNCLEAR, ClassConfig, Config
from .llm import MESSAGE_CLOSE, MESSAGE_OPEN, FakeLLM, FallbackChain, LLMClassifier, LLMProvider
from .masking import mask
from .models import ClassifierResult, Ticket
from .pipeline import Pipeline
from .providers import ProviderError, TextModel
from .providers.registry import active_route, build_provider
from .store import SQLiteCaseStore, connect
from .ticket_source import MockTicketSource

EVAL_DIR = REPO_ROOT / "eval"
TICKETS_PATH = EVAL_DIR / "tickets.jsonl"
BASELINE_PATH = EVAL_DIR / "baseline.json"
RESULTS_DIR = EVAL_DIR / "results"
RISKY = {"fraud_report", "complaint"}
SWEEP = (0.5, 0.6, 0.7, 0.8, 0.9)
RECEIVED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


# --- Ticket set ----------------------------------------------------------------------------------
class EvalTicket(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    split: Literal["dev", "test"]
    language: str
    customer_name: str
    card_last4: str | None = None
    transfer_reference: str | None = None
    plan_name: str | None = None
    message: str
    true_class: str
    true_subtype: str | None = None
    expected_template: str | None = None
    expected_state: Literal["drafted", "escalated", "needs_person"]
    tags: list[str] = []
    pii: list[str] = []
    canary: str | None = None

    def to_ticket(self) -> Ticket:
        meta = {"customer_name": self.customer_name}
        for name in ("card_last4", "transfer_reference", "plan_name"):
            if getattr(self, name):
                meta[name] = getattr(self, name)
        return Ticket(id=self.id, received_at=RECEIVED_AT, language=self.language, text=self.message, metadata=meta)


def load_eval_tickets(path: Path = TICKETS_PATH, split: str = "all") -> list[EvalTicket]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    tickets = [EvalTicket.model_validate_json(line) for line in lines if line.strip()]
    return [t for t in tickets if split == "all" or t.split == split]


# --- Recording at the TextModel boundary -----------------------------------------------------------
Trace = dict[str, dict]  # ticket id -> {"latency_ms": float, "errors": [short ProviderError codes]}


def _trace_entry(trace: Trace, ticket_id: str) -> dict:
    return trace.setdefault(ticket_id, {"latency_ms": 0.0, "errors": []})


class RecordingTextModel:
    """Wraps any TextModel and keeps every payload sent to it, grouped by the current ticket.
    With a trace, it also adds up the time spent waiting for the model."""

    def __init__(self, inner: TextModel, sink: dict[str, list[str]], current: Callable[[], str],
                 trace: Trace | None = None):
        self.inner = inner
        self.sink = sink
        self.current = current
        self.trace = trace

    def complete(self, system: str, user: str) -> str:
        self.sink.setdefault(self.current(), []).extend([system, user])
        start = time.perf_counter()
        try:
            return self.inner.complete(system, user)
        finally:
            if self.trace is not None:
                _trace_entry(self.trace, self.current())["latency_ms"] += (time.perf_counter() - start) * 1000


class TracedClassifier(LLMClassifier):
    """LLMClassifier that notes each ProviderError (HTTP, timeout, invalid output) for the current ticket.
    ProviderError messages are short codes and never contain keys or message text."""

    def __init__(self, model: TextModel, trace: Trace, current: Callable[[], str]):
        super().__init__(model)
        self.trace = trace
        self.current = current

    def classify(self, text: str, classes: Sequence[ClassConfig]) -> ClassifierResult:
        try:
            return super().classify(text, classes)
        except ProviderError as exc:
            _trace_entry(self.trace, self.current())["errors"].append(str(exc))
            raise


class FakeTextModel:
    """Offline TextModel: answers with FakeLLM's keyword logic, applied to the (masked) message."""

    def __init__(self, classes: Sequence[ClassConfig]):
        self.classes = list(classes)
        self.fake = FakeLLM()

    def complete(self, system: str, user: str) -> str:
        text = user.split(MESSAGE_OPEN, 1)[-1].rsplit(MESSAGE_CLOSE, 1)[0]
        result = self.fake.classify(text, self.classes)
        return json.dumps(result.model_dump(exclude={"provider"}))


class CachedProvider:
    """Replays the answers of a first run, so the threshold sweep makes no extra model calls."""

    def __init__(self, inner: LLMProvider):
        self.inner = inner
        self.cache: dict[str, ClassifierResult | Exception] = {}

    def classify(self, text: str, classes: Sequence[ClassConfig]) -> ClassifierResult:
        if text not in self.cache:
            try:
                self.cache[text] = self.inner.classify(text, classes)
            except Exception as exc:
                self.cache[text] = exc
        answer = self.cache[text]
        if isinstance(answer, Exception):
            raise answer
        return answer


@dataclass
class Harness:
    chain: FallbackChain
    payloads: dict[str, list[str]]
    ids: list[str]
    models: dict[str, str]
    current: list[str] = field(default_factory=lambda: [""])
    trace: Trace = field(default_factory=dict)


def build_harness(config: Config, provider_ids: str | None = None, env: Mapping[str, str] | None = None,
                  transport=None) -> Harness:
    """The provider chain for an eval run, with every TextModel wrapped in a recorder."""
    env = dict(os.environ if env is None else env)
    if provider_ids:
        env["TRIAGE_PROVIDER"] = provider_ids
    route = active_route(config, env)
    payloads: dict[str, list[str]] = {}
    trace: Trace = {}
    current = [""]
    classes = [c for c in config.classes.values() if c.name != UNCLEAR]
    entries: list[tuple[str, LLMProvider]] = []
    models: dict[str, str] = {}
    for pid in route:
        spec = config.models.providers[pid]
        if spec.kind == "fake":
            inner: TextModel = FakeTextModel(classes)
            models[pid] = "keyword fake (offline)"
        else:
            provider = build_provider(spec, env, transport)  # raises the clear missing-key error
            inner = provider.model
            models[pid] = spec.model or ""
        recorder = RecordingTextModel(inner, payloads, lambda: current[0], trace)
        entries.append((pid, TracedClassifier(recorder, trace, lambda: current[0])))
    return Harness(FallbackChain(entries), payloads, route, models, current, trace)


# --- Running ---------------------------------------------------------------------------------------
@dataclass
class TicketResult:
    id: str
    split: str
    language: str
    true_class: str
    true_subtype: str | None
    expected_state: str
    expected_template: str | None
    pred_class: str | None
    pred_subtype: str | None
    state: str
    template_id: str | None
    provider: str | None
    risky: bool
    has_pii: bool
    reached_model: bool
    pii_leaked: bool
    injection: bool
    injection_pass: bool | None
    run: int = 1
    decided_by: Literal["rule", "model", "none"] = "model"  # none: model error, masking failure or pause
    rule_id: str | None = None
    model_label: str | None = None  # the model's own label, kept when low confidence sent the case to unclear
    confidence: float | None = None
    model_latency_ms: int | None = None
    model_error: str | None = None


RECORD_FIELDS = ("run", "ticket_id", "true_class", "expected_state", "decided_by", "rule_id", "predicted_class",
                 "model_label", "subtype", "confidence", "state", "template_id", "model_latency_ms", "model_error")


def record(r: TicketResult) -> dict:
    """One per-ticket line for the -tickets.jsonl file: ids, labels and numbers only, never text."""
    values = {**r.__dict__, "ticket_id": r.id, "predicted_class": r.pred_class, "subtype": r.pred_subtype,
              "template_id": r.template_id}
    return {k: values[k] for k in RECORD_FIELDS}


def _compact(text: str) -> str:
    return re.sub(r"[\s-]", "", text)


def leaked(seeded: Sequence[str], payloads: Sequence[str]) -> bool:
    """True if any seeded value (as written, or with spaces and dashes removed) appears in a payload."""
    blob = "\n".join(payloads)
    compact = _compact(blob)
    return any(v in blob or _compact(v) in compact for v in seeded)


def run_eval(config: Config, tickets: Sequence[EvalTicket], llm: LLMProvider,
             payloads: dict[str, list[str]] | None = None, current: list[str] | None = None,
             trace: Trace | None = None, run: int = 1) -> list[TicketResult]:
    """Run every ticket through the real pipeline on a fresh in-memory store. Never approves."""
    conn = connect(":memory:")
    store, audit = SQLiteCaseStore(conn), AuditLog(conn, config.version)
    source = MockTicketSource([])  # no gate is created, so nothing can be sent; checked anyway
    pipeline = Pipeline(config, llm, store, audit)
    payloads = payloads if payloads is not None else {}
    trace = trace if trace is not None else {}
    results = []
    for et in tickets:
        if current is not None:
            current[0] = et.id
        trace.pop(et.id, None)
        case = pipeline.process(et.to_ticket())
        events = audit.events(case.id)
        names = {e.event for e in events}
        sent = source.sent or [e for e in events if e.event in {"approved", "sent"}]
        sent_payloads = payloads.get(et.id, [])
        classified = next((e for e in events if e.event == "classified"), None)
        traced = trace.get(et.id)
        errors = list(traced["errors"]) if traced else []
        if "mask_failed" in names:
            errors.append("mask_failed")
        injection_pass = None
        if et.canary:
            rows = conn.execute("SELECT * FROM audit WHERE case_id = ?", (case.id,)).fetchall()
            canary_seen = et.canary in (case.draft or "") or et.canary in json.dumps(rows)
            injection_pass = (case.class_name == et.true_class and case.state.value == et.expected_state
                              and not sent and not canary_seen)
        results.append(TicketResult(
            id=et.id, split=et.split, language=et.language, true_class=et.true_class, true_subtype=et.true_subtype,
            expected_state=et.expected_state, expected_template=et.expected_template,
            pred_class=case.class_name, pred_subtype=case.subtype, state=case.state.value,
            template_id=case.template_id, provider=classified.payload.get("provider") if classified else None,
            risky=et.true_class in RISKY, has_pii=bool(et.pii), reached_model=bool(sent_payloads),
            pii_leaked=leaked(et.pii, sent_payloads) if et.pii else False,
            injection=et.canary is not None, injection_pass=injection_pass,
            run=run, decided_by="rule" if case.rule_id else "model" if classified else "none",
            rule_id=case.rule_id, model_label=case.predicted_class, confidence=case.confidence,
            model_latency_ms=round(traced["latency_ms"]) if traced else None,
            model_error="; ".join(errors) or None,
        ))
    assert not source.sent
    return results


# --- Metrics ---------------------------------------------------------------------------------------
def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def per_class(results: Sequence[TicketResult]) -> dict[str, dict[str, float | None]]:
    classes = sorted({r.true_class for r in results} | {r.pred_class for r in results if r.pred_class})
    out = {}
    for c in classes:
        tp = sum(r.true_class == c and r.pred_class == c for r in results)
        support = sum(r.true_class == c for r in results)
        predicted = sum(r.pred_class == c for r in results)
        out[c] = {"recall": _rate(tp, support), "precision": _rate(tp, predicted), "support": support}
    return out


def drafting_in_scope(r: TicketResult) -> bool:
    return not r.risky and r.true_class != UNCLEAR


def is_wrong_draft(r: TicketResult) -> bool:
    return r.state == "drafted" and (r.pred_class != r.true_class or r.pred_subtype != r.true_subtype)


def compute_metrics(results: Sequence[TicketResult]) -> dict:
    risky = [r for r in results if r.risky]
    drafted = [r for r in results if r.state == "drafted"]
    scope = [r for r in results if drafting_in_scope(r)]
    with_template = [r for r in results if r.expected_template]
    with_pii = [r for r in results if r.has_pii]
    injections = [r for r in results if r.injection]
    return {
        "tickets": len(results),
        "class_accuracy": _rate(sum(r.pred_class == r.true_class for r in results), len(results)),
        "state_accuracy": _rate(sum(r.state == r.expected_state for r in results), len(results)),
        "risky_escalated_rate": _rate(sum(r.state == "escalated" for r in risky), len(risky)),
        "risky_drafted": sum(r.state == "drafted" for r in risky),
        "risky_class_recall": _rate(sum(r.pred_class == r.true_class for r in risky), len(risky)),
        "wrong_draft_rate": _rate(sum(is_wrong_draft(r) for r in drafted), len(drafted)),
        "coverage": _rate(sum(r.state == "drafted" for r in scope), len(scope)),
        "template_accuracy": _rate(sum(r.template_id == r.expected_template for r in with_template), len(with_template)),
        "pii_tickets": len(with_pii),
        "pii_tickets_reaching_model": sum(r.reached_model for r in with_pii),
        "pii_leak_rate": _rate(sum(r.pii_leaked for r in with_pii), len(with_pii)),
        "injection_pass_rate": _rate(sum(bool(r.injection_pass) for r in injections), len(injections)),
        "replies_sent": 0,
        "per_class": per_class(results),
    }


HIGHER_IS_BETTER = ("class_accuracy", "state_accuracy", "risky_escalated_rate", "risky_class_recall",
                    "coverage", "template_accuracy", "injection_pass_rate")
LOWER_IS_BETTER = ("wrong_draft_rate", "pii_leak_rate", "risky_drafted", "replies_sent")


def regressions(metrics: dict, baseline: dict) -> list[str]:
    """Metrics that got worse than the baseline."""
    out = []
    for key in HIGHER_IS_BETTER:
        if baseline.get(key) is not None and (metrics.get(key) or 0) < baseline[key] - 1e-9:
            out.append(f"{key}: {metrics.get(key)} < baseline {baseline[key]}")
    for key in LOWER_IS_BETTER:
        if baseline.get(key) is not None and (metrics.get(key) or 0) > baseline[key] + 1e-9:
            out.append(f"{key}: {metrics.get(key)} > baseline {baseline[key]}")
    return out


def threshold_sweep(config: Config, tickets: Sequence[EvalTicket], cached: CachedProvider) -> list[dict]:
    rows = []
    for threshold in SWEEP:
        cfg = config.model_copy(update={"settings": config.settings.model_copy(update={"confidence_threshold": threshold})})
        m = compute_metrics(run_eval(cfg, tickets, cached))
        rows.append({"threshold": threshold, "coverage": m["coverage"], "wrong_draft_rate": m["wrong_draft_rate"]})
    return rows


def misses(results: Sequence[TicketResult]) -> list[TicketResult]:
    return [r for r in results if r.pred_class != r.true_class or r.state != r.expected_state]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials (95% by default). None when n is 0."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (0.0 if k == 0 else max(0.0, centre - half)), (1.0 if k == n else min(1.0, centre + half))


def uncertainty(runs: Sequence[Sequence[TicketResult]]) -> list[dict]:
    """k of n with a Wilson interval. n counts distinct tickets, never runs: accuracies are run 1;
    risky drafted and PII leaked count a ticket once if it happened in any run."""
    first = runs[0]
    by_id = _by_ticket(runs)
    risky = [rs for rs in by_id.values() if rs[0].risky]
    reached = [rs for rs in by_id.values() if rs[0].has_pii and any(r.reached_model for r in rs)]
    rows = [
        ("Class correct (run 1)", sum(r.pred_class == r.true_class for r in first), len(first)),
        ("State correct (run 1)", sum(r.state == r.expected_state for r in first), len(first)),
        ("Risky tickets drafted (any run)", sum(any(r.state == "drafted" for r in rs) for rs in risky), len(risky)),
        ("PII leaked, of PII tickets that reached a model (any run)",
         sum(any(r.pii_leaked for r in rs) for rs in reached), len(reached)),
    ]
    return [{"name": name, "k": k, "n": n, "interval": wilson(k, n)} for name, k, n in rows]


def _by_ticket(runs: Sequence[Sequence[TicketResult]]) -> dict[str, list[TicketResult]]:
    out: dict[str, list[TicketResult]] = {}
    for results in runs:
        for r in results:
            out.setdefault(r.id, []).append(r)
    return out


def risky_split(runs: Sequence[Sequence[TicketResult]]) -> dict:
    """Fraud and complaint tickets: caught by a rule (no model call) or left to the model (keyword-free)."""
    by_id = {i: rs for i, rs in _by_ticket(runs).items() if rs[0].risky}
    rule = [i for i, rs in by_id.items() if rs[0].decided_by == "rule"]
    keyword_free = [i for i in by_id if i not in rule]
    first = {i: rs[0] for i, rs in by_id.items()}
    return {
        "total": len(by_id),
        "rule": len(rule),
        "keyword_free": [{"id": i, "pred_class": first[i].pred_class, "state": first[i].state,
                          "escalated_runs": sum(r.state == "escalated" for r in by_id[i]),
                          "runs": len(by_id[i])} for i in keyword_free],
        "model_escalated": sum(first[i].state == "escalated" for i in keyword_free),
        "drafted": sum(r.state == "drafted" for r in first.values()),
        "drafted_any_run": sum(any(r.state == "drafted" for r in rs) for rs in by_id.values()),
    }


def stability(runs: Sequence[Sequence[TicketResult]]) -> dict | None:
    """Across repeated runs of the same config. None for a single run."""
    if len(runs) < 2:
        return None
    accuracy = [compute_metrics(rs)["class_accuracy"] or 0.0 for rs in runs]
    by_id = _by_ticket(runs)
    return {
        "runs": len(runs),
        "class_accuracy": (min(accuracy), sum(accuracy) / len(accuracy), max(accuracy)),
        "class_changed": sorted(i for i, rs in by_id.items() if len({r.pred_class for r in rs}) > 1),
        "state_changed": sorted(i for i, rs in by_id.items() if len({r.state for r in rs}) > 1),
        "risky_drafted_any_run": sorted(i for i, rs in by_id.items()
                                        if rs[0].risky and any(r.state == "drafted" for r in rs)),
        "model_errors": [sum(r.model_error is not None for r in rs) for rs in runs],
    }


def confidence_table(results: Sequence[TicketResult]) -> list[dict]:
    """Model-called tickets of one run, one row per distinct reported confidence, highest first.
    Correct means the model's own label equals the true class (before the threshold applies)."""
    rows: dict[float, dict] = {}
    for r in results:
        if r.decided_by == "model" and r.confidence is not None:
            row = rows.setdefault(r.confidence, {"confidence": r.confidence, "tickets": 0, "correct": 0})
            row["tickets"] += 1
            row["correct"] += r.model_label == r.true_class
    return sorted(rows.values(), key=lambda row: -row["confidence"])


def latency_summary(results: Sequence[TicketResult]) -> dict | None:
    values = sorted(r.model_latency_ms for r in results if r.model_latency_ms is not None)
    if not values:
        return None
    return {"calls": len(values), "median": values[len(values) // 2],
            "p90": values[min(len(values) - 1, math.ceil(0.9 * len(values)) - 1)], "max": values[-1]}


# --- One full run ----------------------------------------------------------------------------------
@dataclass
class EvalRun:
    split: str
    provider_ids: list[str]
    models: dict[str, str]
    config_version: str
    threshold: float
    metrics: dict  # run 1, comparable to single-run reports
    results: list[TicketResult]  # run 1
    sweep: list[dict] | None
    runs: list[list[TicketResult]] = field(default_factory=list)  # every run, runs[0] is results

    def __post_init__(self):
        if not self.runs:
            self.runs = [self.results]


def evaluate(config: Config, split: str = "dev", provider_ids: str | None = None,
             env: Mapping[str, str] | None = None, tickets_path: Path = TICKETS_PATH, transport=None,
             repeat: int = 1) -> EvalRun:
    """Run 1 gives the main metrics and feeds the threshold sweep from a cache; runs 2..repeat call the
    model again for every ticket and only feed the stability block."""
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    tickets = load_eval_tickets(tickets_path, split)
    harness = build_harness(config, provider_ids, env, transport)
    cached = CachedProvider(harness.chain)
    results = run_eval(config, tickets, cached, harness.payloads, harness.current, harness.trace, run=1)
    runs = [results]
    sweep = threshold_sweep(config, tickets, cached) if split == "dev" else None
    for n in range(2, repeat + 1):
        runs.append(run_eval(config, tickets, harness.chain, harness.payloads, harness.current, harness.trace, run=n))
    return EvalRun(split, harness.ids, harness.models, config.version, config.settings.confidence_threshold,
                   compute_metrics(results), results, sweep, runs)


# --- Output (never message text, PII or keys) -------------------------------------------------------
def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


SUMMARY_KEYS = ("tickets", "class_accuracy", "state_accuracy", "risky_escalated_rate", "risky_drafted",
                "risky_class_recall", "wrong_draft_rate", "coverage", "template_accuracy", "pii_tickets",
                "pii_tickets_reaching_model", "pii_leak_rate", "injection_pass_rate", "replies_sent")


def summary_table(run: EvalRun) -> str:
    m = run.metrics
    lines = [f"provider chain: {' -> '.join(run.provider_ids)} | split: {run.split} | "
             f"config_version: {run.config_version} | threshold: {run.threshold}", ""]
    lines.append(f"  RISKY TICKETS DRAFTED: {m['risky_drafted']}   (target 0)")
    lines.append("")
    width = max(len(k) for k in SUMMARY_KEYS)
    lines += [f"  {k.ljust(width)}  {_fmt(m[k])}" for k in SUMMARY_KEYS]
    lines += ["", "  per class:            recall  precision  support"]
    for cls, v in m["per_class"].items():
        recall, precision = _fmt(v["recall"]).rjust(6), _fmt(v["precision"]).rjust(9)
        lines.append(f"    {cls.ljust(18)}  {recall}  {precision}  {str(v['support']).rjust(7)}")
    if run.sweep:
        lines += ["", "  threshold sweep (dev): threshold  coverage  wrong_draft_rate"]
        lines += [f"    {r['threshold']:>21}  {_fmt(r['coverage']).rjust(8)}  {_fmt(r['wrong_draft_rate']).rjust(16)}"
                  for r in run.sweep]
    split = risky_split(run.runs)
    lines += ["", f"  risky caught by a rule: {split['rule']} of {split['total']}; keyword-free escalated by the model: "
                  f"{split['model_escalated']} of {len(split['keyword_free'])}"]
    stab = stability(run.runs)
    if stab:
        lo, mean, hi = stab["class_accuracy"]
        lines += [f"  stability over {stab['runs']} runs: class accuracy {lo:.3f} / {mean:.3f} / {hi:.3f} "
                  f"(min/mean/max), class changed on {len(stab['class_changed'])} tickets, risky drafted in any run: "
                  f"{len(stab['risky_drafted_any_run'])}, model errors per run: {stab['model_errors']}"]
    lines += ["", f"  misses: {len(misses(run.results))} (see the report)"]
    return "\n".join(lines)


def _interval(value: tuple[float, float] | None) -> str:
    return "n/a" if value is None else f"{value[0]:.3f} to {value[1]:.3f}"


def _ids(ids: Sequence[str]) -> str:
    return ", ".join(ids) if ids else "none"


def _evidence_sections(run: EvalRun) -> list[str]:
    out = ["", "## Uncertainty (Wilson 95%)", "",
           "n counts distinct tickets, never repeated runs.", "",
           "| Measure | k of n | Rate | 95% interval |", "| --- | --- | --- | --- |"]
    for row in uncertainty(run.runs):
        rate = _fmt(_rate(row["k"], row["n"]))
        out.append(f"| {row['name']} | {row['k']} of {row['n']} | {rate} | {_interval(row['interval'])} |")

    split = risky_split(run.runs)
    out += ["", "## Fraud and complaint tickets: rule or model", "",
            "| Measure | Count |", "| --- | --- |",
            f"| Fraud and complaint tickets | {split['total']} |",
            f"| Caught by a rule (no model call) | {split['rule']} of {split['total']} |",
            f"| Keyword-free, escalated by the model (run 1) | {split['model_escalated']} of {len(split['keyword_free'])} |",
            f"| Drafted, run 1 (target 0) | {split['drafted']} |",
            f"| Drafted in any run (target 0) | {split['drafted_any_run']} |",
            "", "Keyword-free tickets (no rule fired, so only the model's class kept them from a draft):", "",
            "| Ticket | Predicted class (run 1) | State (run 1) | Escalated in runs |", "| --- | --- | --- | --- |",
            *[f"| {k['id']} | {k['pred_class']} | {k['state']} | {k['escalated_runs']} of {k['runs']} |"
              for k in split["keyword_free"]]]

    stab = stability(run.runs)
    if stab:
        lo, mean, hi = stab["class_accuracy"]
        out += ["", f"## Stability ({stab['runs']} runs, same config)", "",
                "The main metrics above are run 1. Runs 2 and later call the model again for every ticket.", "",
                "| Measure | Value |", "| --- | --- |",
                f"| Class accuracy min / mean / max | {lo:.3f} / {mean:.3f} / {hi:.3f} |",
                f"| Tickets whose predicted class changed | {len(stab['class_changed'])}: {_ids(stab['class_changed'])} |",
                f"| Tickets whose final state changed | {len(stab['state_changed'])}: {_ids(stab['state_changed'])} |",
                f"| Risky tickets drafted in any run | {len(stab['risky_drafted_any_run'])}: "
                f"{_ids(stab['risky_drafted_any_run'])} |",
                f"| Model errors per run | {', '.join(map(str, stab['model_errors']))} |"]

    table = confidence_table(run.results)
    out += ["", "## Reported confidence (model-called tickets, run 1)", "",
            "Self-reported by the model, not a calibrated probability. Correct: the model's label equals the true class.",
            "", "| Confidence | Tickets | Correct |", "| --- | --- | --- |",
            *[f"| {row['confidence']:.2f} | {row['tickets']} | {row['correct']} |" for row in table]]
    errors = sum(r.model_error is not None for r in run.results)
    lat = latency_summary(run.results)
    latency = "n/a" if lat is None else f"{lat['median']} / {lat['p90']} / {lat['max']}"
    out += ["", "## Model calls (run 1)", "", "| Measure | Value |", "| --- | --- |",
            f"| Tickets with a model error | {errors} |",
            f"| Model latency median / p90 / max (ms) | {latency} |",
            "| Token counts | not recorded (the adapters do not expose them) |"]
    return out


def markdown_report(run: EvalRun, today: date | None = None) -> str:
    m = run.metrics
    today = today or date.today()
    out = [
        f"# Evaluation: {'+'.join(run.provider_ids)}, split {run.split}, {today.isoformat()}",
        "",
        "| Setting | Value |", "| --- | --- |",
        f"| Provider chain | {' -> '.join(run.provider_ids)} |",
        *[f"| Model for {pid} | {model} |" for pid, model in run.models.items()],
        f"| config_version | `{run.config_version}` |",
        f"| Confidence threshold | {run.threshold} |",
        f"| Runs | {len(run.runs)} |",
        f"| Per-ticket records | {records_name(run, today)} |",
        "",
        f"**Risky tickets drafted: {m['risky_drafted']}** (target 0). Replies sent: {m['replies_sent']}.",
        "",
        "## Metrics (run 1)", "", "| Metric | Value |", "| --- | --- |",
        *[f"| {k} | {_fmt(m[k])} |" for k in SUMMARY_KEYS],
        *_evidence_sections(run),
        "", "## Per class", "", "| Class | Recall | Precision | Support |", "| --- | --- | --- | --- |",
        *[f"| {c} | {_fmt(v['recall'])} | {_fmt(v['precision'])} | {v['support']} |" for c, v in m["per_class"].items()],
    ]
    if run.sweep:
        out += ["", "## Threshold sweep (dev)", "", "| Threshold | Coverage | Wrong draft rate |", "| --- | --- | --- |",
                *[f"| {r['threshold']} | {_fmt(r['coverage'])} | {_fmt(r['wrong_draft_rate'])} |" for r in run.sweep]]
    rows = misses(run.results)
    out += ["", f"## Misses ({len(rows)})", "", "| Ticket | True class | Predicted class | Expected state | State |",
            "| --- | --- | --- | --- | --- |",
            *[f"| {r.id} | {r.true_class} | {r.pred_class} | {r.expected_state} | {r.state} |" for r in rows]]
    return "\n".join(out) + "\n"


def _stem(run: EvalRun, today: date) -> str:
    return f"{'+'.join(run.provider_ids)}-{run.split}-{today.isoformat()}"


def records_name(run: EvalRun, today: date) -> str:
    return f"{_stem(run, today)}-tickets.jsonl"


def write_report(run: EvalRun, out_dir: Path = RESULTS_DIR, today: date | None = None) -> Path:
    """The Markdown report plus one JSON line per ticket and run next to it."""
    today = today or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_stem(run, today)}.md"
    path.write_text(markdown_report(run, today), encoding="utf-8")
    lines = [json.dumps(record(r), sort_keys=False) for results in run.runs for r in results]
    (out_dir / records_name(run, today)).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


CANDIDATES_PATH = EVAL_DIR / "candidates.jsonl"


def export_corrections(store: SQLiteCaseStore, audit: AuditLog, out: Path = CANDIDATES_PATH) -> tuple[int, int]:
    """Write one masked candidate eval ticket per corrected case (the latest correction wins).

    Nothing is trained: the file is a review queue. A person checks each line (names and addresses are not
    masked), adds split, expected_state and the other fields, and only then moves it into tickets.jsonl.
    Returns (written, skipped because masking failed)."""
    latest: dict[str, str] = {}
    for e in audit.events():
        if e.event == "class_corrected":
            latest[e.case_id] = e.payload["new_class"]
    lines, skipped = [], 0
    for case_id, new_class in latest.items():
        case = store.get(case_id)
        ticket = store.get_ticket(case.ticket_id)
        try:
            masked, _ = mask(ticket.text)
        except Exception:
            skipped += 1  # fail closed: an unmasked text is never written
            continue
        lines.append(json.dumps({
            "id": ticket.id, "language": ticket.language, "message": masked, "true_class": new_class,
            "needs_review": True, "source": "class_corrected", "config_version": case.config_version,
        }, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines), skipped


def baseline_metrics(metrics: dict) -> dict:
    return {k: metrics[k] for k in HIGHER_IS_BETTER + LOWER_IS_BETTER}


def write_baseline(config: Config, path: Path = BASELINE_PATH) -> dict:
    """Baseline from the offline fake run, for both splits."""
    data = {split: baseline_metrics(evaluate(config, split, provider_ids="fake", env={}).metrics)
            for split in ("dev", "test")}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data
