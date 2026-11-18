"""Command line: serve (reviewer console), eval (evaluation set), classify (one-off live check),
export-corrections (reviewer corrections to a masked candidate file for human review)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import DEFAULT_PACK
from .config import UNCLEAR, ConfigError, load_config
from .masking import mask
from .providers import ProviderError
from .providers.registry import build_llm


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="triage_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    p_cls = sub.add_parser("classify", help="mask one message and classify it with the active provider chain")
    p_cls.add_argument("text")
    p_cls.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_eval = sub.add_parser("eval", help="run the evaluation set and write a Markdown report")
    p_eval.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    p_eval.add_argument("--provider", help="comma-separated provider ids (default: the active routing)")
    p_eval.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_eval.add_argument("--out", type=Path, default=None, help="report folder (default eval/results)")
    p_eval.add_argument("--write-baseline", action="store_true", help="update eval/baseline.json from the fake run")
    p_eval.add_argument("--repeat", type=_positive, default=1,
                        help="run the set N times; metrics are run 1, the report adds a stability block")
    p_exp = sub.add_parser("export-corrections",
                           help="write reviewer class corrections, masked, to a candidate file for human review")
    p_exp.add_argument("--db", default=None, help="SQLite case store (default: TRIAGE_DB)")
    p_exp.add_argument("--out", type=Path, default=None, help="output file (default eval/candidates.jsonl)")
    p_serve = sub.add_parser("serve", help="run the reviewer console on http://127.0.0.1:8000")
    p_serve.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    p_serve.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside a container")
    p_serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        if args.command == "classify":
            return classify(args.pack, args.text)
        if args.command == "eval":
            return run_eval_command(args.pack, args.split, args.provider, args.out, args.write_baseline, args.repeat)
        if args.command == "export-corrections":
            return export_corrections_command(args.db, args.out)
        return serve(args.pack, args.host, args.port)
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


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def run_eval_command(pack: Path, split: str, provider: str | None, out: Path | None, write_baseline: bool,
                     repeat: int = 1) -> int:
    from . import evaluation

    config = load_config(pack)
    if write_baseline:
        evaluation.write_baseline(config)
        print(f"baseline written to {evaluation.BASELINE_PATH}")
    run = evaluation.evaluate(config, split, provider_ids=provider, repeat=repeat)
    print(evaluation.summary_table(run))
    path = evaluation.write_report(run, out or evaluation.RESULTS_DIR)
    print()
    print(f"report: {path}")
    return 0


def export_corrections_command(db: str | None, out: Path | None) -> int:
    """Reviewer corrections -> masked candidate tickets. No model is trained; a person reviews the file."""
    import os

    from . import evaluation
    from .audit import AuditLog
    from .store import SQLiteCaseStore, connect

    db = db or os.environ.get("TRIAGE_DB", "")
    if not db or db == ":memory:" or not Path(db).is_file():
        print("export-corrections needs an existing SQLite case store: pass --db or set TRIAGE_DB", file=sys.stderr)
        return 2
    conn = connect(db)
    path = out or evaluation.CANDIDATES_PATH
    written, skipped = evaluation.export_corrections(SQLiteCaseStore(conn), AuditLog(conn, "export"), path)
    print(f"{written} corrected case(s) written to {path} for human review"
          + (f"; {skipped} skipped because masking failed" if skipped else ""))
    return 0


def serve(pack: Path, host: str, port: int) -> int:
    import uvicorn

    from .web.app import create_app

    # DB path from TRIAGE_DB (default in-memory), reviewer id from TRIAGE_REVIEWER.
    uvicorn.run(create_app(pack=pack), host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
