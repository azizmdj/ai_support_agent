# Support Triage Agent

An unofficial pitch demo of a support triage agent for a bank, built on synthetic data only. It is not a production system: no authentication, no real customer data, no security review.

Code rules run first, a model only classifies, replies come from approved templates, and a person approves every reply before it is sent.

```mermaid
flowchart LR
  M[Customer message] --> R["Rules · Code"]
  R -- "fraud or complaint keyword (no AI call)" --> E["Escalated · Code"]
  R -- no rule --> P["Mask personal data · Code"] --> C["Classify · AI"]
  P -- masking fails --> N["Needs a person · Reviewer"]
  C -- locked class --> E
  C -- "low confidence or error" --> N
  C -- confident --> T["Pick and fill template · Code"]
  N -- reviewer sets class --> T
  T -- "missing field or language" --> N
  T --> D["Draft · Code"]
  D --> V["Approve, edit or escalate · Reviewer"]
  V -- escalate --> E
  E --> K["Take case · Reviewer"]
  V -- approve --> G["Approval gate · Code"] --> S[Reply sent]
```

## Quickstart

With [uv](https://docs.astral.sh/uv/), offline (fake model, no keys):

```
uv sync
uv run pytest
uv run python -m triage_agent serve             # reviewer console on http://127.0.0.1:8000
uv run python -m triage_agent demo              # same flow on the command line
uv run python -m triage_agent classify "<text>" # masked text and model result
uv run python -m triage_agent eval --split dev  # evaluation set, writes a report to eval/results/
```

With Docker: `docker compose up --build`, then open http://127.0.0.1:8000 (health check: http://127.0.0.1:8000/health).

With a real model, set the key in your terminal only (PowerShell shown; it is never written to a file):

```
$env:OPENAI_API_KEY = Read-Host -Prompt "OpenAI API key"
$env:TRIAGE_PROVIDER = "openai_main"
uv run python -m triage_agent serve      # or: docker compose up --build
```

## How Finom adapts it

| What to change | Where |
| --- | --- |
| Message classes, priorities, teams, deadlines, locked classes | `pack/taxonomy.yaml` |
| Keyword and regex rules | `pack/rules.yaml` |
| Approved reply templates, per language | `pack/templates/<lang>/<id>.md` |
| Confidence threshold, allowed template fields, AI-disclosure line | `pack/settings.yaml` |
| Models and fallback order (no code change) | `pack/models.yaml`, or `TRIAGE_PROVIDER` |
| A model API that is not OpenAI-compatible or Anthropic | a small `TextModel` adapter in `src/triage_agent/providers/` |
| Ticket system | a `TicketSource` adapter (`src/triage_agent/ticket_source.py`) |
| Case storage (default SQLite) | a `CaseStore` adapter (`src/triage_agent/store.py`) |
| Console test messages | `pack/scenarios.yaml` |
| Own labeled tickets for evaluation | `eval/tickets.jsonl` |

The config is validated on every start; a broken pack is rejected with a list of errors.

## Results

gpt-4o-mini on the 38 held-out test tickets: 0 of 11 fraud and complaint tickets drafted, 0 PII leaks, 37 of 38 classes correct. 100 synthetic tickets written by an LLM; see [docs/project.md](docs/project.md) for the full results and limits.

- The tickets are synthetic; real messages will be messier.
- The sets are small: one ticket moves a test metric by 2.6 points.
- Only structured personal data (IBAN, card, email, phone) is masked and measured; names and addresses are not.
- The model's self-reported confidence is uncalibrated; safety comes from rules, locked classes and the approval gate.

## Before production

See the checklist under "Before production" in [docs/project.md](docs/project.md).

Demo video: (link to be added)

## Repo layout

```
src/triage_agent/   pipeline, rules, approval gate, audit log, masking, evaluation, CLI
  providers/        model adapters (OpenAI-compatible, Anthropic)
  web/              reviewer console (server-rendered, no JavaScript)
pack/               classes, rules, settings, templates, scenarios, models
eval/               100 evaluation tickets, baseline, reports
tests/              tests and the 15 console fixture tickets
docs/project.md     design, results, decisions
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```
