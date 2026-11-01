"""Command line: `python -m triage_agent demo` runs the fixture tickets offline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import DEFAULT_PACK, DEFAULT_TICKETS
from .audit import AuditLog
from .config import load_config
from .gate import Gate
from .config import UNCLEAR, ConfigError
from .masking import mask
from .providers import ProviderError
from .providers.registry import build_llm
from .models import CaseState
from .pipeline import Pipeline
from .store import SQLiteCaseStore, connect
from .ticket_source import MockTicketSource, load_tickets


def demo(pack: Path, tickets_path: Path) -> int:
    config = load_config(pack)
    conn = connect(":memory:")
    store, audit = SQLiteCaseStore(conn), AuditLog(conn, config.version)
    source = MockTicketSource(load_tickets(tickets_path))
    llm = build_llm(config)
    pipeline = Pipeline(config, llm, store, audit)

    cases = [pipeline.process(t) for t in source.fetch_new()]
    print(f"config_version {config.version}  |  {len(cases)} tickets  |  model: {llm.describe()}  |  in-memory SQLite\n")
    rows = [("ticket", "class", "state", "reason", "due_at (UTC)")]
    rows += [
        (c.ticket_id, c.class_name or "", c.state.value, c.reason, c.due_at.strftime("%Y-%m-%d %H:%M") if c.due_at else "")
        for c in cases
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    for i, row in enumerate(rows):
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))

    drafted = next((c for c in cases if c.state == CaseState.DRAFTED), None)
    if drafted is None:
        print("\nno drafted case to approve")
        return 1
    Gate(store, audit, source).approve(drafted.id, "demo", drafted.draft_hash or "")
    ticket_id, body = source.sent[-1]
    print(f"\nreviewer 'demo' approved {drafted.id} (template {drafted.template_id}); reply sent to {ticket_id}:\n")
    print("    " + body.rstrip().replace("\n", "\n    "))
    print(f"\naudit rows written: {len(audit.events())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="triage_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    p_demo = sub.add_parser("demo", help="run the fixture tickets through the pipeline")
    p_demo.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_demo.add_argument("--tickets", type=Path, default=DEFAULT_TICKETS)
    p_cls = sub.add_parser("classify", help="mask one message and classify it with the active provider chain")
    p_cls.add_argument("text")
    p_cls.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_eval = sub.add_parser("eval", help="run the evaluation set and write a Markdown report")
    p_eval.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    p_eval.add_argument("--provider", help="comma-separated provider ids (default: the active routing)")
    p_eval.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_eval.add_argument("--out", type=Path, default=None, help="report folder (default eval/results)")
    p_eval.add_argument("--write-baseline", action="store_true", help="update eval/baseline.json from the fake run")
    p_serve = sub.add_parser("serve", help="run the reviewer console on http://127.0.0.1:8000")
    p_serve.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        if args.command == "classify":
            return classify(args.pack, args.text)
        if args.command == "eval":
            return run_eval_command(args.pack, args.split, args.provider, args.out, args.write_baseline)
        if args.command == "serve":
            return serve(args.pack, args.port)
        return demo(args.pack, args.tickets)
    except ConfigError as exc:  # names a missing variable, never a value
        print(exc, file=sys.stderr)
        return 2


def classify(pack: Path, text: str) -> int:
    """One-off live check. Runs only the classifier (no rules, no draft, nothing stored or sent)."""
    config = load_config(pack)
    llm = build_llm(config)
    print(f"model chain: {llm.describe()}")
    print(f"masked text: {mask(text)[0]}")
    classes = [c for c in config.classes.values() if c.name != UNCLEAR]
    try:
        result = llm.classify(text, classes)
    except ProviderError as exc:
        print(f"provider error: {exc}", file=sys.stderr)
        return 1
    print(f"result: {result.model_dump_json()}")
    return 0


def run_eval_command(pack: Path, split: str, provider: str | None, out: Path | None, write_baseline: bool) -> int:
    from . import evaluation

    config = load_config(pack)
    if write_baseline:
        evaluation.write_baseline(config)
        print(f"baseline written to {evaluation.BASELINE_PATH}")
    run = evaluation.evaluate(config, split, provider_ids=provider)
    print(evaluation.summary_table(run))
    path = evaluation.write_report(run, out or evaluation.RESULTS_DIR)
    print()
    print(f"report: {path}")
    return 0


def serve(pack: Path, port: int) -> int:
    import uvicorn

    from .web.app import create_app

    # DB path from TRIAGE_DB (default in-memory), reviewer id from TRIAGE_REVIEWER.
    uvicorn.run(create_app(pack=pack), host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
