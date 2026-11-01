# Support Triage Agent

An unofficial pitch demo of a support triage agent for a bank, built on synthetic data only. It is not a production system.

```mermaid
flowchart LR
  M[Customer message] --> R["Rules · Code"]
  R -- "fraud or complaint keyword (no AI call)" --> E["Escalated · Code"]
  R -- no rule --> C["Classify · AI"]
  C -- locked class --> E
  C -- "low confidence or error" --> N["Needs a person · Reviewer"]
  C -- confident --> T["Pick and fill template · Code"]
  N -- reviewer sets class --> T
  T -- "missing field or language" --> N
  T --> D["Draft · Code"]
  D --> V["Approve, edit or escalate · Reviewer"]
  V -- escalate --> E
  E --> K["Take case · Reviewer"]
  V -- approve --> G["Approval gate · Code"] --> S[Reply sent]
```

```
uv sync
uv run pytest
uv run python -m triage_agent serve   # reviewer console on http://127.0.0.1:8000
uv run python -m triage_agent demo    # same flow on the command line
```

Design, scope and plan: [docs/project.md](docs/project.md)
