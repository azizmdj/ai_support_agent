# AI Support Triage

A demo system that sorts a bank's customer messages and drafts replies. Code applies the rules, an AI model only labels the message, and a person approves every reply. Synthetic data only.

## How it is used

1. A customer message arrives.
2. Rules check it first. Fraud and complaint keywords go straight to a person, with no AI call.
3. PIIs are masked, and the AI picks a class (card blocked, pending transfer, fee question, ...).
4. Code fills the approved reply template for that class. 
5. A reviewer approves, edits or escalates. Only then is the reply sent.

The console has three tabs: **Setup** (how it works, read-only), **Review** (the daily queue) and **Monitor** (counts and the latest evaluation).

## Why it is useful

- **Faster answers.** The reviewer doesn't have to find the right template: routine questions arrive classified, with the approved reply already filled in.
- **Flexible.** Classes, keyword rules and reply templates are plain files, so a team can change them without touching code. 
- **Consistent and auditable.** Drafts use approved templates only, and every step is logged. Seeded PIIs never reached a model (0 leaks in the eval).

Class accuracy on 38 unseen test tickets: 0.947–0.974 with gpt-4o-mini (5 runs) and 0.895 with Claude Haiku (3 runs). Details: [docs/project.md](https://github.com/azizmdj/ai_support_agent/blob/main/docs/project.md).

## How it is built

```mermaid
flowchart LR
  M[Message] --> R["Rules · code"]
  R -- fraud or complaint keyword --> P["Person takes the case"]
  R -- no rule --> A["Mask and classify · AI"]
  A -- unsure, error or locked class --> P
  A -- confident --> T["Approved template · code"]
  T --> V["Reviewer approves · person"] --> S["Send · code gate"]
```

- Python, FastAPI and SQLite in one container.
- Model adapters for OpenAI-compatible APIs (also local servers) and Anthropic, with a fallback chain.
- Classes, rules, templates and settings are plain files in `pack/`, validated on every start.
- The AI cannot send, choose actions or call tools. If it fails, rules and the human queue still work.

## Compliance and privacy

Built with GDPR, the EU AI Act and DORA in mind (personal data masked before model calls, human approval of every reply, an AI-disclosure line, an audit log without message text): it uses synthetic data only, and names and addresses are not masked.

## Run and adapt

```
docker compose up --build    # console at http://127.0.0.1:8000, offline test model
```

Without Docker: `uv sync`, then `uv run python -m triage_agent serve`. For a real model, set `OPENAI_API_KEY` and `TRIAGE_PROVIDER=openai_main` in your shell (provider ids are in `pack/models.yaml`). Evaluate with `uv run python -m triage_agent eval --split dev` (add `--provider openai_main` for a real model).

It runs on your own infrastructure: one container and SQLite; the only outside service is the model you choose. With a local model (any OpenAI-compatible server such as Ollama or vLLM, set via `base_url`), no message leaves your network. Hosted models need an EU endpoint, a data-processing agreement and zero-retention settings.

| To change | Where |
| --- | --- |
| Classes, deadlines, locked classes | `pack/taxonomy.yaml` |
| Keyword rules | `pack/rules.yaml` |
| Reply templates, per language | `pack/templates/<lang>/` |
| Threshold, allowed fields, AI note | `pack/settings.yaml` |
| Models and fallback order | `pack/models.yaml` |
| Ticket system or database | a `TicketSource` or `CaseStore` adapter in `src/triage_agent/` |

Restart after a config change and check that the config version on Setup changed. Run `eval` on your own labeled tickets before you trust it.

## Limits

- The tickets are synthetic and few (38 test tickets)
- No login, security review or load test. The local-model path is tested with mocked HTTP only.
- A wrong-class draft can still reach the reviewer, who approves every reply.

Before production: see the checklist in [docs/project.md](docs/project.md). Demo video: (link to be added)
