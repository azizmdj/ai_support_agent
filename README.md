# Support Triage Agent (Phase 1: headless core)

Pitch demo, not a production system. Code rules run first, a (fake) LLM classifies the rest, replies come only from approved templates, and nothing is sent without a recorded human approval. All data is synthetic.

```
uv sync
uv run pytest
uv run python -m triage_agent demo
```
