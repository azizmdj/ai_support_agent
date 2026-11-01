# Support Triage Agent: Project Document

Oct 31, 2026 · @Aziz M

## Status

| Phase | State | What it contains |
| --- | --- | --- |
| 1 | Built | Core pipeline, rules, approval gate, audit log, config validator, offline demo |
| 2 | Built | Reviewer console, journey trail, scenario buttons, pause switch, escalate, wrong class. 129 tests in total |
| 3 | Planned | OpenAI-compatible client, PII masking with leak rate, ~150 synthetic tickets, evaluation suite |
| 4 | Planned | docker-compose, README, hosted demo |

## Problem

A bank receives hundreds to thousands of customer messages a day, and every one must be sorted, routed and answered correctly and quickly. The mix includes fraud reports, blocked cards, pending transfers, fee questions, complaints and general questions.

Three kinds of mistake are costly:

- A fraud report that waits in the general queue can cost the customer money.
- A complaint that misses its response deadline is a regulatory problem.
- A wrong answer about fees or limits creates disputes.

Sorting and first-draft answers are repetitive work, which makes them a good fit for automation, as long as the risky cases stay with people.

## Solution in one view

Code handles what must be exact, an LLM handles language, and a human decides anything with consequences. The agent speeds up sorting and drafting and never acts alone.

| Step | Who does it | What happens | Why |
| --- | --- | --- | --- |
| 1. Rules | Code | Urgent keywords, deadline timers, routing | Exact, cheap, always the same result |
| 2. Classify | LLM | Labels messages the rules cannot catch | Needs language understanding |
| 3. Pick template | Code | Loads the approved template for the class (the classifier picks the sub-type when a class has several; an unknown sub-type gets the default template) | Replies use approved wording only |
| 4. Fill template | Code | Fills the approved template with fields such as name, card ending and transfer reference, taken only from ticket metadata | Regulated wording stays approved |
| 5. Approve | Human | Approves, edits or escalates every reply, or corrects the class; takes fraud and complaint cases | The reply affects the customer |

The language comes from the ticket's `language` field. There is no language detection.

Fraud and complaint cases skip the draft step and go straight to a person. There is no LLM summary: the reviewer sees the rule or model reason plus the original message. Rules always override the LLM.

## Scope

The demo shows that a triage agent can be built, evaluated and deployed in a way a bank could adapt. It is not a production system, and it says so.

**The demo covers**

- Config-driven message classes, rules and policies
- Swappable adapters for ticket system, model and case store
- A human approval gate enforced in code, not only in the prompt
- An audit log of every classification, draft, edit, approval and send
- A reviewer console: one server-rendered page with plain forms
- A plain Python package with SQLite that runs on a laptop; tests run with the network blocked
- An evaluation suite with accuracy numbers (planned, phase 3)
- docker-compose for a server or hosted demo (planned, phase 4)

**The demo does not claim**

- A production security review, authentication or access control
- Load testing at a real bank's volume
- Model-governance or data-protection sign-off
- Use of any real customer data: all data is synthetic

## Architecture

Every message passes through code rules first; the LLM only classifies, replies come from approved templates, and a person approves before anything is sent.

&#91;embedded content: architecture · 8 components, 2 paths\]

Fraud and complaint messages go straight to a person: at the rules step when a keyword matches, or after classification because their class is locked. If the model is down, rules and the human queue still work.

## Adapting it to Finom

Finom can take the code and change behavior through configuration files and small adapters, without touching the agent logic.

| What Finom wants to change | How it works |
| --- | --- |
| Add or edit message classes | Classes live in one `taxonomy.yaml`: name, description, priority, target team, deadline, risk flag and reply mode (template or no draft). A new class is a new block in that file. |
| Edit reply templates | Approved reply templates are Markdown files with YAML front-matter, one folder per language (`templates/en`, `templates/de`). Finom imports its existing templates, and every case and audit entry names the template it used. |
| Add their own rules | `rules.yaml` holds keyword and regex rules with an action: `escalate` (straight to a person, no draft) or `route` (force the class, normal drafting). Rules run before the LLM and always win. When a rule fires, the LLM is not called. |
| Connect their ticket system | A `TicketSource` interface with `fetch_new` and `send_reply`. We ship an in-memory mock; Finom writes a small adapter for the real system. |
| Use their own models | An `LLMProvider` interface with one method, `classify`, that returns a label, an optional sub-type, a confidence and a reason. We ship a deterministic fake. One OpenAI-compatible client for hosted APIs and local servers such as Ollama and vLLM, where switching is a base URL in config (planned, phase 3). |
| Use their own data stores | A `CaseStore` interface: cases (`save`, `get`, `list`), tickets (`save_ticket`, `get_ticket`) and the pause flag (`is_paused`, `set_paused`). The default is SQLite. A Postgres swap (planned, later). Templates are plain files; pointing the template source at a bank's existing template system (planned, later). |
| Other markets and languages | Languages are template folders plus an AI-disclosure line per language in `settings.yaml`. Separate market folders (planned, later). |
| Change the demo scenarios | `scenarios.yaml` in the pack holds the test messages behind the console's scenario buttons, two variants each. |

A real class entry from `pack/taxonomy.yaml` shows how little a new class takes:

```yaml
- name: card_blocked
  description: The customer's card is blocked, lost, stolen or declined.
  priority: high
  team: cards_support
  deadline: PT4H
  risk: normal
  draft_mode: template
  subtypes: [lost_or_stolen, blocked_after_pin_attempts]
  templates:
    - {id: card_blocked_lost, when: lost_or_stolen}
    - {id: card_blocked_pin, when: blocked_after_pin_attempts}
    - {id: card_blocked_general, default: true}
```

## Configuration and privacy

Business rules and data privacy are both controlled by configuration, with safety rails that configuration cannot remove.

**Business-logic configuration**

- **Validate before activating.** The validator runs on every config load and rejects a broken pack with a list of errors. Running the evaluation set on the new config to show accuracy before it goes live is planned (phase 3).
- **Add a class without code.** A new class needs a description and an approved reply template. The classifier receives the class list from the taxonomy; building the real classifier prompt from it is planned (phase 3).
- **Version every config.** The config version is stored on each case and on every audit row, so every decision can be traced to the rules that produced it. Editing any pack file, including a template, changes the version. Config lives in git with review before merge.
- **Lock the safety rails.** A class is locked by the `risk: locked` flag, never by its name. A locked class cannot draft, and a rule on a locked class can only escalate. A renamed fraud class with the flag is still locked.
- **Rules.** Each rule has an id, patterns, optional languages, an action (`escalate` or `route`) and a target class. Matching ignores case and accents and treats ß as ss, so Betrug, BETRUG and betrügerisch all match. When several rules fire, the class with the highest priority wins.
- **Abstain when unsure.** A built-in `unclear` class and a confidence threshold of 0.7 send low-confidence messages to the human queue. A label outside the taxonomy, an invalid model output or a model error also go to a person.

**What the validator rejects**

- A locked class with a draft mode other than `none`
- A rule on a locked class with an action other than `escalate`
- A rule or template pointing to an unknown class
- A template file whose id is not in the taxonomy, or whose class does not match it
- A template id in the taxonomy with no template file
- Duplicate class names, including a class named `unclear`, and duplicate rule ids
- A bad deadline duration or an invalid regex
- A drafting class without exactly one default template, or a template tied to an unknown sub-type
- A template whose front-matter id or language does not match its file location, or that is not marked `synthetic: true`
- A template field outside the allowed fields in `settings.yaml`, or a placeholder not listed in `required_fields`
- A template language without an AI-disclosure line

**Data privacy for model calls**

- **Mask everything first (planned, phase 3).** All message text is masked before any model call. IBANs, card numbers, emails and phone numbers become placeholders such as `<IBAN_1>`. Models only classify and never write drafts, so nothing needs restoring.
- **Fail closed (planned, phase 3).** If masking fails, the model call does not happen.
- **What reaches a model.** A message that matches a fraud keyword never reaches a model, because the LLM is not called when a rule fires. A complaint without a keyword may reach the model, masked.
- **No message text in the audit log.** Audit rows hold only ids, class names, confidence, rule id, template id, hashes and short codes. The code rejects any other payload field. The case store keeps the customer message text so the reviewer can read it; the audit log never does. A configurable retention period (planned, later).
- **Measure leakage (planned, phase 3).** The evaluation suite seeds synthetic tickets with fake personal data and reports the share that reaches a model call (the PII leak rate). The IBANs are fictional but checksum-valid, so the detector is really tested.
- **Route by sensitivity (planned, later).** Each class sets which models may see it.

Automatic detection misses things, especially names and free-text addresses, and masked text can still identify a person under DSGVO. Masking reduces risk but does not remove it. For real use, a bank would add an EU endpoint, a data processing agreement and zero-retention settings with the provider.

Detection will use pattern rules with checksum validation for IBANs and card numbers (planned, phase 3). Name detection is not part of the first build, and the README will state this limit.

## Product design

The reviewer console is the product: customers only see the reply, so the reviewer's screen should make a routine ticket one action. The console is built. Every button calls an action in the approval gate.

**Who uses it**

- **Support reviewer** (most of the time): approves, edits, escalates or corrects the class, and takes fraud and complaint cases.
- **Team lead** (occasionally): adds classes and templates by editing the config files. The config is validated on every load; one command that also shows accuracy before and after is planned (phase 3).
- **Customer:** receives faster, consistent replies in the usual channel. Code appends a short note to every draft saying the reply was prepared with AI assistance and reviewed by the team.

**Reviewer console: one screen, one queue**

&#91;embedded content: reviewer console · one card per ticket\]

The header shows "Unofficial demo, synthetic data", the reviewer id, the pause switch and the scenario buttons. Three lanes on the left show the business flow, with a count on each, and each ticket is one decision card in the center.

- **Urgent:** escalated cases, sorted by deadline, with a relative due time such as "due in 45m" or "overdue by 5m".
- **Ready to approve:** a drafted case. The draft sits in an editable text box with one primary button, **Approve and send**. If the text was changed, the server logs an edit and then the approval.
- **Needs a person:** unclear or low-confidence messages, model errors, paused tickets and drafts blocked by a missing field or language. There is no draft; the reviewer picks the class, which creates one.
- Drafted and needs-a-person cards also offer **Wrong class** (a class list and a button) and **Escalate**.
- Fraud and complaint cards have no template. They show the rule or model reason, the original message and one button, **Take case**.
- After any action the next ticket opens automatically: Urgent first, then Ready, then Needs a person. A one-line banner confirms the action, such as "Reply sent via mock ticket system."
- Sent and taken cases leave the lanes. An empty queue says "Queue is empty."
- A stale form (the draft changed since the page loaded) is rejected with a clear message, and nothing is sent.

**Journey trail**

The top of each card shows the ticket's path as six steps in a strip that wraps on narrow screens:

| Step | Actor | Example result |
| --- | --- | --- |
| 1. Message received | Customer | received time |
| 2. Rules | Code | "rule fraud_keywords fired", "no rule matched", "held: paused" |
| 3. Classify | AI | "fee_question 0.90", "skipped: rule fired", "failed: model error" |
| 4. Template | Code | template id, or "no draft: missing card_last4" |
| 5. Review | Reviewer | "approved by …", "taken by …", or waiting |
| 6. Reply | Customer | "sent, with AI-assistance note", "not sent yet", "no reply: handled by a person" |

Each step has a written status: Done, Skipped, Your turn or Pending. The trail is built only from the case and its audit rows, so it adds no new storage.

**Trust features**

- A one-line reason on every card: the rule that fired, or the model's class and confidence.
- The journey trail, so a first-time visitor sees who did what.
- A collapsed Details section with the case's audit entries. Showing the masked message the AI saw (planned, phase 3).
- Wrong class is one click and doubles as labeled feedback: a `class_corrected` audit row with the old and new class.
- A pause switch in the header. While it is on, rules still run and fraud still escalates, the LLM is not called, and everything else goes to Needs a person with the reason "paused". Switching it writes a `pause_on` or `pause_off` audit row.
- State is always written as text, never shown by color alone.

**Demo experience**

`uv run python -m triage_agent serve` starts the console at http://127.0.0.1:8000. On first start with an empty database it loads the 15 fixture tickets, shifted in time so the newest arrives "just now".

A bar of four buttons after the label "Send a test message:" (Routine question, Fraud report, Complaint, Hostile message) creates a new ticket, runs it through the pipeline and opens its card. Each button rotates between an English and a German variant. A reviewer sees flexibility, banking fit and safety in two minutes without typing anything. When the queue is empty, the card area shows the same six-step trail in a generic version, each step Pending with a one-line description, plus "Send a test message above."

`uv run python -m triage_agent demo` still runs the fixture tickets offline without the console, prints a table of class, state, reason and deadline, approves one draft and prints the sent reply.

**Left out on purpose**

- A config page. Classes and templates are edited as files, and changing one live is itself a good demo moment.
- A customer-facing chat window, which is a different product with different risks.
- Dashboards and a done-today history.
- An automatic instant acknowledgment for fraud reports. It would send something without approval, so it is an open question for Finom.

## Compliance map

The design addresses DSGVO, the EU AI Act and DORA through concrete features, and it does not claim formal compliance. That needs Finom's own legal and risk review.

| Regulation | Concern | How the design responds |
| --- | --- | --- |
| DSGVO | Personal data in messages | Synthetic data only; no message text in the audit log; PII masking before any model call (planned, phase 3); EU hosting (planned, phase 4); a configurable retention period (planned, later) |
| EU AI Act | Transparency and human oversight | Every draft carries an AI-disclosure line appended by code; the gate enforces human approval of every outbound reply; the use case avoids high-risk categories (no credit decisions, no employee scoring or monitoring); every decision is logged |
| DORA | ICT third-party risk and resilience | The model sits behind a swappable interface; a fallback provider (planned, phase 3); a tested "model unavailable" path (rules still run, and provider errors go to a person with a `model_error` audit event); audit log built; incident log and health checks (planned, phase 4) |

**Regulator note.** [Finom is licensed as an electronic money institution by De Nederlandsche Bank](https://finom.co/llms.txt), so DNB and Dutch rules are likely the main supervisory frame, with national rules such as BaFin's relevant for the German market. This should be confirmed with Finom's compliance team before the document describes any regulator in detail.

**Risk classification assumption.** Message triage with human approval is assumed not to be a high-risk AI system under the EU AI Act. Finom's compliance team would need to confirm this for their specific use.

## Synthetic data and evaluation

All data is synthetic, committed to the repo, and used to measure the agent with numbers. This is what separates a demo from a prototype.

**Reply templates:** currently 8 templates (6 English, 2 German) in neutral fictional-bank wording, each marked `synthetic: true`. The target is about 12, written as a fictional Finom-style help center and clearly labeled synthetic, not real Finom policy, with facts based on public product pages: plans, cards, SEPA transfers, e-invoicing, support tiers, fees, verification and the complaint process (planned, phase 3).

**Tickets:** currently 15 handwritten tickets in German and English (`tests/fixtures/tickets.jsonl`). The expected class and state of each is checked in a test. The target is about 150 messages generated with a strong model across about 6 classes, each with a true class and the correct reply template, and about 50 held out as a test set (planned, phase 3). Both sets include hard cases:

- Two issues in one message
- Vague or very short messages
- Out-of-scope requests such as tax advice (planned, phase 3)
- Prompt-injection attempts, for example a message telling the agent to ignore its instructions

All personal data is fake, such as made-up names and IBANs that are fictional but checksum-valid.

**Metrics (run with pytest, planned, phase 3):**

| Metric | What it shows |
| --- | --- |
| Classification accuracy and per-class recall | Whether messages go to the right team |
| Fraud and complaint recall | Whether risky cases are never missed |
| Template selection | Whether the agent picks the right approved template |
| Escalation correctness | Whether risky classes always reach a human |
| Injection resistance | Whether hostile messages change the agent's behavior |
| Approval-gate test | That nothing is sent without a recorded approval |

Phase 1 already has unit tests for escalation, injection resistance and the approval gate. Finom can add its own labeled tickets, rerun the suite, and see accuracy before trusting the agent with anything.

## Deployment and operations

The core is a plain Python package with SQLite. It runs on a laptop with no network, and Finom can import it into its own services.

- **Packaging:** an installable Python package (`pyproject.toml`, managed with `uv`). docker-compose, with settings and secrets from environment variables (planned, phase 4).
- **Console settings:** `TRIAGE_DB` sets the SQLite file (default: in memory, so data is lost on restart), and `TRIAGE_REVIEWER` sets the reviewer id (default `demo-reviewer`). The console binds to 127.0.0.1:8000. There is no authentication beyond this one fixed reviewer id.
- **Hosting for the public demo:** an EU provider, so no data leaves the EU (planned, phase 4).
- **Observability:** the audit log of every classification, template choice, draft, edit, approval and send. Structured logs (planned, phase 4). No external tracing service is required.
- **Failure handling:** if the model raises an error or returns invalid output, rules still run and the case goes to a person with a `model_error` audit event.

**Running on Finom's own infrastructure**

- Finom imports the package and sets a base URL for its own model, local or hosted (client planned, phase 3).
- Storage is local SQLite.
- Tests run with the network blocked: any socket use fails, so a test that tries to reach the internet fails.

When the model runs inside the bank's network, the data does not leave it, and PII masking is mainly needed for hosted-model setups.

## Development plan

The plan is the smallest build that shows flexibility, integrability, banking fit and safety, using basic components that can be swapped.

**What to build**

| What it must show | What the build includes |
| --- | --- |
| Flexibility | `taxonomy.yaml`, `rules.yaml`, `settings.yaml` and template files; a new class needs no code (built) |
| Integrability | Three adapter interfaces (ticket source, LLM provider, case store) with mock versions and SQLite storage (built); one OpenAI-compatible client for hosted and local models (planned, phase 3); a Postgres swap (planned, later) |
| Banking fit | Fraud and complaint escalation to a person, approved templates only, an audit log with reason and config version (built) |
| Safety | Approval gate, locked safety rails and a global pause switch (built); PII masking with a leak rate and evaluation numbers (planned, phase 3) |
| Deployment | A Python package that runs on a laptop (built); docker-compose and a hosted demo (planned, phase 4) |

**Repo structure**

```
src/triage_agent/       pipeline, rules, approval gate, audit log, config validator, adapter interfaces, commands (demo, serve)
src/triage_agent/web/   reviewer console: FastAPI app, journey trail, Jinja templates, one CSS file
pack/                   taxonomy, rules, settings, templates, scenarios
tests/                  unit and console tests, fixture tickets
README.md
pyproject.toml
eval/                   metrics and test sets (planned, phase 3)
deploy/                 Dockerfile, docker-compose (planned, phase 4)
```

The console uses plain HTML forms rendered on the server: no JavaScript, no CDN and no external fonts. Every GET only reads; every action is a POST.

**Order of work**

1. **Contracts, core and command line** with the pack and fixture tickets. When the tests pass, the hard part is done. (built, phase 1)
2. **Console,** including the journey trail and scenario buttons so visitors can try it without typing real data. (built, phase 2)
3. **Model client, PII masking, synthetic tickets and the evaluation suite.** (planned, phase 3)
4. **Deployment and README:** docker-compose and the hosted demo; architecture, how Finom adapts it, and what is needed before production. (planned, phase 4)

Keep Finom-specific wording in the pack files and not in the core, so a second pack is a copy. One exception today: the fake LLM's keyword list lives in the core.

**Storage and integrations**

- The default is basic: SQLite for cases, tickets, the pause flag and the audit log, and plain template files loaded by id.
- Both sit behind interfaces. A Postgres case store and a template source that points at a bank's existing template system (planned, later).
- A contract test that runs the same suite on SQLite and Postgres (planned, later).
- Messages are processed as they arrive. There is no queue and no search index, because the class decides the template.

**Pipeline**

The pipeline is plain Python: rules, classify, pick template, fill template. It gets no ticket source, so it cannot send anything. Case state lives in the database. While the pause switch is on, only rules with action `escalate` take effect; the LLM is not called and every other ticket goes to a person.

Approval is a separate gate, not a pipeline step. The gate holds every reviewer action, and it is the only code that calls `send_reply`.

- **Approve** requires a drafted case, a reviewer id and the hash of the current draft. It writes an `approved` audit row, sends, then writes `sent`.
- **Edit** changes the draft (new hash, still drafted).
- **Escalate** moves a drafted or needs-a-person case to escalated and removes the draft.
- **Correct class** writes a `class_corrected` row, then redrafts from the new class's default template. A locked class goes to escalated; a missing field or language goes to needs a person.
- **Take** closes an escalated case.
- **Pause** switches the flag and writes `pause_on` or `pause_off`.

None of these except approve sends anything. The console only calls the gate; it has no access to the ticket system.

Case states (as in `models.py`):

| From | Can move to |
| --- | --- |
| `new` | `drafted`, `needs_person`, `escalated` |
| `drafted` | `sent` (only through approve), `escalated`, `needs_person`, `drafted` (redraft) |
| `needs_person` | `drafted`, `escalated`, `needs_person` |
| `escalated` | `taken` |

The steps map directly to graph nodes if Finom uses an orchestrator.

**Deployment**

- **Hosted demo (planned, phase 4):** docker-compose on one small EU server (for example Hetzner in Germany or Finland) with TLS through a reverse proxy. It uses a hosted model API, an access code for the people the link is shared with, and a low spend limit on the API key.
- **Finom's own infrastructure:** import the package and set the model base URL, described under Deployment and operations.

**Local testing**

The whole system runs on a laptop with no API keys.

- `uv sync` installs it, `uv run pytest` runs the tests, `uv run python -m triage_agent serve` starts the console, and `uv run python -m triage_agent demo` runs the command-line demo.
- A **fake LLM provider** classifies by keywords or returns scripted answers, so tests are fast and repeatable.
- Both the console and the demo load the fixture tickets and templates into SQLite, in memory by default.
- Console tests use FastAPI's in-process test client, so they also run with the network blocked.
- `docker compose up` for the API and console (planned, phase 4).

**Mandatory QA tests**

These will run in CI on every push and block merging when they fail (CI planned, phase 4). Today they run with `uv run pytest`.

| Test | What it proves | State |
| --- | --- | --- |
| Approval gate | Nothing is sent without a recorded approval | Built |
| Safety rails | A config that allows automatic reply for a locked class is rejected, even after renaming it | Built |
| Rules override the LLM | A fraud message is escalated even when the fake model says otherwise, and the model is not called | Built |
| PII masking and leak rate | Masking round-trips, a masking error blocks the model call, and structured identifiers (IBAN, card, email, phone) in the seeded set never reach a model call | Planned, phase 3 |
| Prompt-injection set | Hostile messages change neither the class nor the actions taken | Built for a small set; full set planned, phase 3 |
| Evaluation regression | Accuracy and fraud and complaint recall stay at or above the agreed baseline | Planned, phase 3 |
| Pause switch | While paused, fraud still escalates, everything else goes to a person, and the model is not called | Built |
| Reviewer console | Scenarios land in the right lane; approve, edit, stale draft, escalate and wrong class behave and log correctly; GET never changes state; customer text is escaped | Built |

Shorter unit tests cover config validation, template filling (a missing field or language blocks the draft), audit-log fields and config version, the model-down path, invalid model output, deadlines, the journey trail and the no-network guard. Storage tests on Postgres are planned. A linter and secret scan in CI (planned, phase 4), and a run with the real model on demand (planned, phase 3).

**Cautions**

- Do not copy Finom's real UI, logo or help-center text. Use a neutral design with their name and colors, label it as an unofficial demo with synthetic data, and write your own templates.
- Publish the core and the generic pack publicly. Share the Finom pack with them directly.

## Decisions

- **Confidence** is self-reported by the model, with a threshold of 0.7. It is not calibrated.
- **Deadlines** are ISO 8601 durations (days, hours, minutes) in calendar time, UTC, counted from `received_at`.
- **A two-issue ticket gets one label.** If the model is unsure between topics, low confidence sends it to a person.
- **`config_version`** is the first 12 characters of a sha256 over all pack files, with line endings normalized.
- **Pause lets only escalate rules through.** The LLM is not called while paused.
- **The console is plain server-rendered forms:** no JavaScript, one fixed reviewer id.

## Before production

The demo is a starting point. Before any real use, a bank would need to cover at least these items.

- [ ] Security review, authentication and role-based access control
- [ ] Data-protection impact assessment, legal review of data flows to model providers, and deletion on request across case store and audit log
- [ ] AI Act risk classification confirmed by the compliance team
- [ ] DORA mapping: ICT third-party register entry for each model provider, resilience testing and incident reporting
- [ ] Complaint deadlines and escalation rules confirmed per market
- [ ] Evaluation on the bank's own labeled tickets, with agreed accuracy thresholds per class
- [ ] Load testing at real message volume
- [ ] Pilot on real tickets with a small reviewer group before wider use
- [ ] Model governance: approved models, version pinning, and a change process

## Sources

- [Finom LLM-readable overview (licensing, products, integrations)](https://finom.co/llms.txt)
- [Finom AI launch coverage, The Paypers (beta capabilities and planned approval-based actions)](https://thepaypers.com/fintech/news/finom-launches-ai-assistant-built-into-sme-accounts)
- [Finom MCP announcement](https://finom.co/en-de/news/finom-mcp-ask-questions-inside-claude-chatgpt/)
