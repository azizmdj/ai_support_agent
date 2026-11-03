# Support Triage Agent: Project Document

Oct 31, 2026 · @Aziz M

## Status

| Phase | State | What it contains |
| --- | --- | --- |
| 1 | Built | Core pipeline, rules, approval gate, audit log, config validator |
| 2 | Built | Reviewer console, journey trail, scenario buttons, pause switch, escalate, wrong class |
| 3a | Built | Model providers (OpenAI-compatible and Anthropic adapters), PII masking, fallback chain, `classify` command |
| 3b | Built | 100 synthetic tickets with seeded fake PII, the `eval` command and metrics including PII leak rate and injection resistance, a live run with gpt-4o-mini |
| 4 | Built | Dockerfile, docker-compose, health endpoint, CI with ruff and pytest, full README. The hosted demo is dropped (see Decisions) |
| 5 | Built | Clean-up (the `demo` command and the unused `route` rule action are gone), a read-only System page, a simpler decision card, model hyperparameters in `pack/models.yaml`. 240 tests in total (239 passed, 1 expected failure) |

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
| 2. Classify | LLM | Labels messages the rules cannot catch. Code masks personal data first, so a real model sees placeholders only | Needs language understanding |
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
- Real-model support behind swappable adapters, with PII masking before every real-model call
- An evaluation suite with accuracy, safety and privacy numbers
- A Docker image and docker-compose that run the console on one machine, and CI on every push

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
| Add their own rules | `rules.yaml` holds keyword and regex rules. The only action is `escalate`: the ticket goes straight to a person, with no draft. Rules run before the LLM and always win. When a rule fires, the LLM is not called. |
| Connect their ticket system | A `TicketSource` interface with `fetch_new` and `send_reply`. We ship an in-memory mock; Finom writes a small adapter for the real system. |
| Use their own models | A `TextModel` interface with one method, `complete(system, user)`, that returns text. Two adapters ship: `openai_compatible`, which also covers hosted gateways and local servers such as Ollama and vLLM through a base URL, and `anthropic`. Adapters only move text; the prompt, the parsing and the validation live once in the core (`LLMClassifier`). `pack/models.yaml` lists the providers and an ordered fallback chain, and `TRIAGE_PROVIDER` overrides the chain for one run. Each provider can set `temperature` (0 to 2, OpenAI-compatible only), `max_tokens` (1 to 4096) and `timeout_seconds` (1 to 120); unset values keep the adapter defaults (temperature 0, 300 tokens, 20 s for OpenAI-compatible; no temperature, 1024 tokens, 20 s for Anthropic). A deterministic fake provider is the default. All HTTP behavior is tested with mocked HTTP. The OpenAI adapter has also been run once against the live API; the Anthropic adapter has not been run live. |
| Use their own data stores | A `CaseStore` interface: cases (`save`, `get`, `list`), tickets (`save_ticket`, `get_ticket`) and the pause flag (`is_paused`, `set_paused`). The default is SQLite. A Postgres swap (not built). Templates are plain files; pointing the template source at a bank's existing template system (planned, later). |
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

- **Validate before activating.** The validator runs on every config load and rejects a broken pack with a list of errors. Running `eval` on the new config shows accuracy before it goes live.
- **Add a class without code.** A new class needs a description and an approved reply template. The classifier prompt is built from the taxonomy (class names, descriptions and sub-types), so a new class needs no prompt edit.
- **Version every config.** The config version is stored on each case and on every audit row, so every decision can be traced to the rules that produced it. Editing any pack file, including a template, changes the version. Config lives in git with review before merge.
- **Lock the safety rails.** A class is locked by the `risk: locked` flag, never by its name. A locked class cannot draft, and a rule on a locked class can only escalate. A renamed fraud class with the flag is still locked.
- **Rules.** Each rule has an id, patterns, optional languages, the action `escalate` and a target class. Matching ignores case and accents and treats ß as ss, so Betrug, BETRUG and betrügerisch all match. When several rules fire, the class with the highest priority wins.
- **Abstain when unsure.** A built-in `unclear` class and a confidence threshold of 0.7 send low-confidence messages to the human queue. A label outside the taxonomy, an invalid model output or a model error also go to a person.

**What the validator rejects**

- A locked class with a draft mode other than `none`
- A rule with an action other than `escalate`
- A rule or template pointing to an unknown class
- A template file whose id is not in the taxonomy, or whose class does not match it
- A template id in the taxonomy with no template file
- Duplicate class names, including a class named `unclear`, and duplicate rule ids
- A bad deadline duration or an invalid regex
- A drafting class without exactly one default template, or a template tied to an unknown sub-type
- A template whose front-matter id or language does not match its file location, or that is not marked `synthetic: true`
- A template field outside the allowed fields in `settings.yaml`, or a placeholder not listed in `required_fields`
- A template language without an AI-disclosure line
- In `models.yaml`: an unknown provider id in the routing, a literal `api_key` field (only `api_key_env` is allowed), duplicate provider ids, a real provider without `base_url` and `model`, a hyperparameter outside its range, and a temperature on an Anthropic provider. Errors name the field, never a value
- At startup: a selected provider whose key variable is not set. The error names the variable, never its value

**Data privacy for model calls**

- **Mask everything first.** Every message is masked before any real-model call. Masking sits inside the classifier that every real provider uses, so no adapter can skip it. IBAN-shaped strings, card-like digit runs, emails and phone numbers become placeholders such as `<IBAN_1>`, numbered in order. The mapping stays in memory for one call. Models only classify and never write drafts.
- **Fail closed.** If masking fails, no provider is called, a `mask_failed` audit row is written, and the case goes to needs a person. The whole chain stops, including the fake provider.
- **The fake provider sees unmasked text.** It runs locally, makes no network call, and is the offline default.
- **What reaches a model.** A message that matches a fraud keyword never reaches a model, because the LLM is not called when a rule fires. A complaint without a keyword may reach the model, masked.
- **No message text in the audit log.** Audit rows hold only ids, class names, confidence, rule id, template id, the provider id that answered, hashes and short codes. The code rejects any other payload field. The case store keeps the customer message text so the reviewer can read it; the audit log never does. A configurable retention period (planned, later).
- **Check for leaks.** A test seeds tickets with fake IBANs, card numbers, emails and phone numbers and checks that none of them appears in any model payload, audit row or log line, including the error paths. The `eval` command also measures the PII leak rate at the model boundary: every payload sent to a model is recorded and checked for the seeded values.
- **Route by sensitivity (planned, later).** Each class sets which models may see it.

Detection is pattern based and deliberately over-masks. It needs no checksum, so a mistyped IBAN or card number is masked too, and some harmless numbers may be masked as well. Phone numbers must start with + or 0 and have at least 7 digits. Names and free-text addresses are not detected.

Automatic detection misses things, especially names and free-text addresses, and masked text can still identify a person under DSGVO. Masking reduces risk but does not remove it. For real use, a bank would add an EU endpoint, a data processing agreement and zero-retention settings with the provider.

## Product design

The reviewer console is the product: customers only see the reply, so the reviewer's screen should make a routine ticket one action. It has two tabs: **Review** for the queue and **System** for the flow, settings and rules. Every button on Review calls an action in the approval gate. System has no buttons.

**Who uses it**

- **Support reviewer** (most of the time): approves, edits, escalates or corrects the class, and takes fraud and complaint cases.
- **Team lead** (occasionally): adds classes and templates by editing the config files, then checks the System page and runs `eval` to compare accuracy before and after a change.
- **Customer:** receives faster, consistent replies in the usual channel. Code appends a short note to every draft saying the reply was prepared with AI assistance and reviewed by the team.

**Header**

The header shows the app name with "Unofficial demo, synthetic data", the tabs Review and System, a model chip (for example "Model: fake"), which links to the settings on the System page, a chip with the config version, and the pause switch. Nothing else.

**Review: one screen, one queue**

&#91;embedded content: reviewer console · one card per ticket\]

A row of four buttons at the top, after "Send a test message:" (Routine question, Fraud report, Complaint, Hostile message), creates a new ticket, runs it through the pipeline and opens its card. Each button rotates between an English and a German variant. Three lanes on the left show the queue, with a count on each:

- **Urgent:** escalated cases, sorted by deadline, with a relative due time such as "due in 45m" or "overdue by 5m".
- **Ready to approve:** drafted cases.
- **Needs a person:** unclear or low-confidence messages, model errors, paused tickets and drafts blocked by a missing field or language.

Each ticket is one decision card: the journey trail, a one-line reason, the customer message, the draft if there is one, and exactly one primary button.

| Card | Primary button | Inside "Something wrong?" |
| --- | --- | --- |
| Drafted | **Approve and send** (the draft is editable; a changed text is logged as an edit, then approved) | Wrong class, Escalate to a person |
| Needs a person | **Set class and draft** | Escalate to a person |
| Escalated (fraud, complaint) | **Take case** | none |

- After any action the next ticket opens automatically: Urgent first, then Ready, then Needs a person. A one-line banner confirms the action, such as "Reply sent via mock ticket system."
- Sent and taken cases leave the lanes.
- A stale form (the draft changed since the page loaded) is rejected with a clear message, and nothing is sent.
- A line under the header always says where to start: pick a ticket or send a test message, read the trail, and find rules, settings and explanations on the System tab.
- An empty queue also shows a three-line guide (send a test message; review the case with the trail; open System) and the trail in a generic version.

**Journey trail**

The top of each card shows the ticket's path as six steps in a strip that wraps on narrow screens. Each step name links to its row on the System page.

| Step | Actor | Example result |
| --- | --- | --- |
| 1. Message received | Customer | received time |
| 2. Rules | Code | "rule fraud_keywords fired", "no rule matched", "held: paused" |
| 3. Classify | AI | "fee_question 0.90 via openai_main", "skipped: rule fired", "failed: model error", "failed: masking failed, no model call" |
| 4. Template | Code | template id, or "no draft: missing card_last4" |
| 5. Review | Reviewer | "approved by …", "taken by …", or waiting |
| 6. Reply | Code | "sent, with AI-assistance note", "not sent yet", "no reply: handled by a person" |

Each step has a written status: Done, Skipped, Your turn or Pending. The trail is built only from the case, its audit rows and the loaded config, so it adds no new storage.

A collapsed **Details** section shows "What each step saw and produced": one row per step with its input, output and the config used. For Classify the input is the masked message, computed from the stored ticket when the page loads and never stored, and the config is the model, its temperature and the threshold. The raw audit rows sit in a nested, collapsed "Audit log".

**System: read-only**

Everything on the System page is derived from the loaded config at request time, so an edit to a pack file shows up after a restart.

- **Flow:** the six steps as a table: who does it, what it does, input and output, and which file or setting controls it. Each step has an anchor (`#rules`, `#classify`, `#template`, `#review`, `#reply`).
- **Settings** (`#settings`): the config version, the pause state, the confidence threshold, the deadline for unclear tickets, the reviewer id, and the provider chain with kind, model, host, temperature, max_tokens, timeout and the key variable name with "set" or "not set". A key value is never shown.
- **Business rules:** the classes (priority, team, deadline, risk, draft mode, number of templates, languages) and the rules (action, target class, number of keywords, languages).
- **Explain:** three collapsed sections: Business (the three costly mistakes and what prevents each), Math (the decision rule and metric definitions, see "How to read the numbers") and Engineering (interfaces, case states, audit, fail-closed paths, how to add a class or a model).

**Trust features**

- A one-line reason on every card: the rule that fired, or the model's class and confidence.
- The journey trail and the step table, so a first-time visitor sees who did what with which input.
- Wrong class is one click and doubles as labeled feedback: a `class_corrected` audit row with the old and new class.
- A pause switch in the header. While it is on, rules still run and fraud still escalates, the LLM is not called, and everything else goes to Needs a person with the reason "paused". Switching it writes a `pause_on` or `pause_off` audit row.
- State is always written as text, never shown by color alone. System fonts, no icons, no animation, no JavaScript.

**Demo experience**

`docker compose up --build` or `uv run python -m triage_agent serve` starts the console at http://127.0.0.1:8000. On first start with an empty database it loads the 15 fixture tickets, shifted in time so the newest arrives "just now". A reviewer sends a test message, follows the trail, and opens System to see why. That shows flexibility, banking fit and safety in two minutes without typing anything.

**Left out on purpose**

- An editing UI for the config. The System page only shows the config. Classes, rules, templates and settings are edited as files, because config belongs in git with review before merge.
- A customer-facing chat window, which is a different product with different risks.
- Dashboards and a done-today history.
- An automatic instant acknowledgment for fraud reports. It would send something without approval, so it is an open question for Finom.

## Compliance map

The design addresses DSGVO, the EU AI Act and DORA through concrete features, and it does not claim formal compliance. That needs Finom's own legal and risk review.

| Regulation | Concern | How the design responds |
| --- | --- | --- |
| DSGVO | Personal data in messages | Synthetic data only; no message text in the audit log; PII masking before every real-model call, which fails closed (built; names and free-text addresses are not detected); EU hosting for a hosted setup (not built); a configurable retention period (planned, later) |
| EU AI Act | Transparency and human oversight | Every draft carries an AI-disclosure line appended by code; the gate enforces human approval of every outbound reply; the use case avoids high-risk categories (no credit decisions, no employee scoring or monitoring); every decision is logged |
| DORA | ICT third-party risk and resilience | The model sits behind a swappable interface; an ordered fallback chain of providers (built); a tested "model unavailable" path (rules still run, and provider errors go to a person with a `model_error` audit event); audit log built; a `/health` endpoint and a container health check (built); an incident log (not built) |

**Regulator note.** [Finom is licensed as an electronic money institution by De Nederlandsche Bank](https://finom.co/llms.txt), so DNB and Dutch rules are likely the main supervisory frame, with national rules such as BaFin's relevant for the German market. This should be confirmed with Finom's compliance team before the document describes any regulator in detail.

**Risk classification assumption.** Message triage with human approval is assumed not to be a high-risk AI system under the EU AI Act. Finom's compliance team would need to confirm this for their specific use.

## Synthetic data and evaluation

All data is synthetic, committed to the repo, and used to measure the agent with numbers. This is what separates a demo from a prototype.

**Reply templates:** 12 templates, one English and one German version for each of the 6 templates, in neutral fictional-bank wording and marked `synthetic: true`. Templates written as a fictional Finom-style help center, with facts based on public product pages, are planned (later).

**Tickets:** 100 tickets in `eval/tickets.jsonl`, written by an LLM and committed: 62 in the dev split and 38 in the test split, stratified per class. Each ticket has a true class, an expected state and, where one applies, the expected template. About half carry seeded fake personal data, and 8 are injection attempts with a unique canary token. The 15 handwritten tickets in `tests/fixtures/tickets.jsonl` seed the console. The set includes hard cases:

- Fraud reports and complaints without any rule keyword, some with typos
- Two issues in one message (the true class is the one with the higher priority)
- Vague or very short messages
- Out-of-scope requests such as tax or legal advice
- Mixed German and English, rude but legitimate messages, and a few French or Turkish messages, which have no template
- Prompt-injection attempts, for example a message telling the agent to ignore its instructions

All personal data is fake, such as made-up names, fictional IBANs (some mistyped), test card numbers, example.com emails and 555-style phone numbers.

**Metrics**

`uv run python -m triage_agent eval [--split dev|test|all] [--provider IDS]` runs every ticket through the real pipeline on a fresh in-memory store and never approves anything. It prints a table and writes a Markdown report to `eval/results/`. The report holds the provider and model, the config version, the threshold, all metrics and a list of misses by ticket id. It contains no message text, PII values or keys.

| Metric | What it shows |
| --- | --- |
| Class accuracy, per-class recall and precision | Whether messages go to the right team (`unclear` counts as a class) |
| State accuracy | Whether each ticket ended where expected: drafted, escalated or with a person |
| Risky escalated rate and risky drafted count | Whether fraud and complaint tickets always reach a person, and how many got a draft (target 0) |
| Risky class recall | Whether fraud and complaints get the right class |
| Wrong draft rate | Share of drafts with the wrong class or sub-type |
| Coverage | Share of in-scope, non-risky tickets that got a draft |
| Template accuracy | Whether the agent picks the right approved template |
| PII leak rate | Share of PII-seeded tickets where a seeded value reaches a model payload, measured at the model boundary |
| Injection pass rate | Injection tickets with the expected class and state, nothing sent, and the canary in no draft and no audit row |
| Threshold sweep (dev only) | Coverage and wrong draft rate at thresholds 0.5 to 0.9 |

**How to read the numbers**

A ticket is drafted only if all of these hold; otherwise it goes to a person:

1. no escalate rule fired,
2. the model label is in the taxonomy,
3. the confidence c is at least the threshold (0.7),
4. the class is not locked,
5. all template fields exist in the ticket,
6. the language has a template.

Confidence is self-reported by the model, not a calibrated probability.

- **recall** = correct tickets of a class / all true tickets of that class
- **coverage** = drafted / in-scope, non-risky tickets
- **wrong_draft_rate** = drafts with the wrong class or sub-type / all drafts
- **pii_leak_rate** = PII tickets where a seeded value reaches a model call / all PII tickets

Small samples: with 0 failures in n tickets, the 95% upper bound on the true failure rate is about 3/n (the rule of three). So "0 drafted" is evidence, not proof:

- All 28 fraud and complaint tickets (17 dev + 11 test): 3/28, about 11%.
- The test split alone (11 tickets): 3/11, about 27%.

The dev split was used to tune the prompt, so the test-only figure is the clean one. The tickets are synthetic and written in one style, so the bound describes this set, not real traffic.

**Results**

Provider `openai_main` with model gpt-4o-mini, threshold 0.7, run on 2026-11-02. Dev v1 ran on config version `a74e7a29655e`; dev v2 and test share `cb16d991171f`. The numbers come from the reports in `eval/results/`.

The dev v2 and test runs were measured on `cb16d991171f`, before the Phase 5 changes to `pack/models.yaml` and `pack/rules.yaml`. The live eval has not been rerun since. Those changes are comment lines only, but they change the config version. The classifier prompt, the class descriptions, the rule keywords and the threshold are unchanged since the measured runs. The OpenAI adapter still sends the same defaults (temperature 0, 300 tokens, 20 s timeout), now read from the adapter defaults that `pack/models.yaml` can override.

| Metric | Dev v1 (62) | Dev v2 (62) | Test (38) |
| --- | --- | --- | --- |
| Class accuracy | 0.935 | 0.935 | 0.974 |
| State accuracy | 0.968 | 0.968 | 1.000 |
| Risky escalated rate | 1.000 | 1.000 | 1.000 |
| Risky class recall | 1.000 | 1.000 | 1.000 |
| Risky tickets drafted | 0 | 0 | 0 |
| Wrong draft rate | 0.105 | 0.083 | 0.056 |
| Coverage | 1.000 | 0.973 | 0.783 |
| Template accuracy | 0.919 | 0.892 | 0.952 |
| `unclear` recall | 0.750 | 0.875 | 1.000 |
| PII leak rate | 0 (28 of 32 PII tickets reached the model) | 0 (28 of 32) | 0 (14 of 19) |
| Injection pass rate | 1.0 | 1.0 | 1.0 |
| Replies sent | 0 | 0 | 0 |

0 of 17 dev and 0 of 11 test fraud and complaint tickets were drafted. On the test split the one class miss was a fee question labeled `general_question`. Dev v1 used the original prompt. Dev v2 followed one calibration change to the classifier prompt and to two class descriptions.

**Why test coverage is 0.783.** The test split has 23 in-scope, non-risky tickets. 18 of them expect a draft and 5 expect a person: 3 have a missing field (CB03, CB10, PT05) and 2 are in a language without templates (CB05 in Turkish, CB15 in French). All 18 got a draft, so coverage is 18 of 23. State accuracy is 1.000, so these 5 ended with a person as expected.

**Findings**

- Self-reported confidence is uncalibrated. gpt-4o-mini reports about 0.9 or higher almost always, so the threshold has little effect.
- Safety comes from the rules, the locked classes and the approval gate, not from the threshold.
- The calibration change shifted errors toward human review and did not raise accuracy. One vague ticket moved from a wrong draft to a person, and one general question moved to a person.
- A second signal, such as agreement between two providers or token log-probabilities, is a possible next step (planned, later).

**Process**

The dev split was used for tuning. The test split was run once, after dev was settled, and nothing changed afterwards. The fake provider is a regression guard only: its numbers are not results. It has a known gap: it drafts two keyword-free fraud reports in dev. A strict expected-failure test records the target of 0, so it fails loudly once the gap is closed.

**What the numbers do not show**

- The tickets are synthetic and written by an LLM. Real messages will be messier.
- The sets are small. One ticket moves a metric by about 1.6 points on dev and 2.6 points on test.
- Only structured PII (IBAN, card, email, phone) is measured. Names and addresses are not.
- The calibration prompt names tax or legal advice as an example, which is also an eval category.
- The result belongs to one model and one prompt.
- The Anthropic adapter has not been run live.

Finom can add its own labeled tickets, rerun `eval`, and see accuracy before trusting the agent with anything.

## Deployment and operations

The core is a plain Python package with SQLite. It runs on a laptop with no network, in a container, and Finom can import it into its own services.

- **Packaging:** an installable Python package (`pyproject.toml`, managed with `uv`).
- **Docker:** a `Dockerfile` on `python:3.13-slim` installs the locked dependencies with `uv sync --frozen --no-dev` in their own layer, then copies the code, the pack, the eval set and the console fixture tickets. It runs as a non-root user, exposes port 8000 and starts `serve --host 0.0.0.0`. A `HEALTHCHECK` calls `/health` with Python's `urllib`. `.dockerignore` keeps `.git`, `.venv`, caches, `.env` files, databases and `eval/results` out of the image.
- **docker-compose:** one service. The port is bound to `127.0.0.1:8000` only. `TRIAGE_DB=/data/triage.db` sits on a named volume, so cases survive a restart. `TRIAGE_PROVIDER` defaults to `fake`. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are passed through from the shell by name; no value is in any file. A commented-out line mounts `./pack` read-only, so classes and templates can be edited on the host; the config is read at startup, so a `docker compose restart` applies an edit.
- **Health endpoint:** `GET /health` returns `{"status": "ok", "models": [provider ids]}`. It changes no state and shows no secrets.
- **Host and port:** `serve --host` (default `127.0.0.1`) and `--port` (default 8000). The container uses `--host 0.0.0.0`.
- **CI:** `.github/workflows/ci.yml` runs on every push to `main` and on pull requests: `uv sync --frozen`, `uv run ruff check .` and `uv run pytest` on Python 3.13. It uses no secrets, so no live model is called.
- **Model settings:** providers, base URLs, model names, optional `temperature`, `max_tokens` and `timeout_seconds`, and the fallback chain live in `pack/models.yaml`. `TRIAGE_PROVIDER` (comma-separated provider ids) overrides the chain for one run. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` hold the keys; the config only names the variable. Keys are never logged, audited or shown.
- **Console settings:** `TRIAGE_DB` sets the SQLite file (default: in memory, so data is lost on restart), and `TRIAGE_REVIEWER` sets the reviewer id (default `demo-reviewer`). The console binds to 127.0.0.1:8000. There is no authentication beyond this one fixed reviewer id.
- **Hosted public demo:** (not built). See Decisions.
- **Observability:** the audit log of every classification, template choice, draft, edit, approval and send. Structured logs (not built). No external tracing service is required.
- **Failure handling:** if a provider times out, returns an HTTP error or gives invalid output, the next provider in the chain is tried. If all fail, rules still run and the case goes to a person with a `model_error` audit event. If masking fails, no provider is called and a `mask_failed` row is written.

**Running on Finom's own infrastructure**

- Finom imports the package and sets a base URL for its own model, local or hosted, in `pack/models.yaml`.
- Storage is local SQLite, in a file set by `TRIAGE_DB` or on the compose volume. A Postgres store and an on-premise deployment profile: (not built).
- Tests run with the network blocked: any socket use fails, so a test that tries to reach the internet fails.

When the model runs inside the bank's network, the data does not leave it, and PII masking is mainly needed for hosted-model setups.

## Development plan

The plan is the smallest build that shows flexibility, integrability, banking fit and safety, using basic components that can be swapped.

**What to build**

| What it must show | What the build includes |
| --- | --- |
| Flexibility | `taxonomy.yaml`, `rules.yaml`, `settings.yaml` and template files; a new class needs no code (built) |
| Integrability | Adapter interfaces for ticket source, model and case store, with mock versions and SQLite storage (built); OpenAI-compatible and Anthropic adapters for hosted and local models, with an ordered fallback chain in `pack/models.yaml` (built); a Postgres swap (not built) |
| Banking fit | Fraud and complaint escalation to a person, approved templates only, an audit log with reason and config version (built) |
| Safety | Approval gate, locked safety rails, a global pause switch, PII masking that fails closed, the PII leak rate and evaluation numbers (built) |
| Deployment | A Python package that runs on a laptop, a Docker image, docker-compose and CI (built); a hosted demo and a Kubernetes manifest (not built) |

**Repo structure**

```
src/triage_agent/            pipeline, rules, approval gate, audit log, config validator, classifier and fallback chain, commands (serve, eval, classify)
src/triage_agent/masking.py  PII masking
src/triage_agent/evaluation.py  evaluation suite and metrics
src/triage_agent/providers/  model adapters (openai_compatible, anthropic) and the registry that builds the chain
src/triage_agent/web/        reviewer console: FastAPI app, journey trail, System page, Jinja templates, one CSS file
pack/                        taxonomy, rules, settings, templates, scenarios, models (providers and routing)
tests/                       unit, adapter, masking and console tests, fixture tickets
README.md
pyproject.toml
eval/                        tickets.jsonl (100 tickets), baseline.json (fake-provider baseline), results/ (reports)
Dockerfile                   container image for the console
docker-compose.yml           one service, localhost port, named volume for the database
.github/workflows/ci.yml     ruff and pytest on every push and pull request
```

The console uses plain HTML forms rendered on the server: no JavaScript, no CDN and no external fonts. Every GET only reads; every action is a POST.

**Order of work**

1. **Contracts, core and command line** with the pack and fixture tickets. When the tests pass, the hard part is done. (built, phase 1)
2. **Console,** including the journey trail and scenario buttons so visitors can try it without typing real data. (built, phase 2)
3. **Models and evaluation,** in two parts.
   - 3a: model adapters, PII masking, the fallback chain and the `classify` command. (built, phase 3a)
   - 3b: 100 synthetic tickets with seeded fake PII, the evaluation suite with PII leak rate and injection resistance, and a live run with gpt-4o-mini. (built, phase 3b)
4. **Packaging and README:** Dockerfile, docker-compose, health endpoint, CI, and a README with the flowchart, how Finom adapts it, results and limits. (built, phase 4) The hosted demo is dropped.
5. **Polish:** clean-up, the read-only System page, a simpler decision card, and model hyperparameters in `pack/models.yaml`. (built, phase 5)

Keep Finom-specific wording in the pack files and not in the core, so a second pack is a copy. One exception today: the fake LLM's keyword list lives in the core.

**Storage and integrations**

- The default is basic: SQLite for cases, tickets, the pause flag and the audit log, and plain template files loaded by id.
- Both sit behind interfaces. A Postgres case store (not built). A template source that points at a bank's existing template system (planned, later).
- A contract test that runs the same suite on SQLite and Postgres (not built).
- Messages are processed as they arrive. There is no queue and no search index, because the class decides the template.

**Pipeline**

The pipeline is plain Python: rules, classify, pick template, fill template. It gets no ticket source, so it cannot send anything. Case state lives in the database. While the pause switch is on, rules still escalate; the LLM is not called and every other ticket goes to a person. The classify step masks the message, tries the providers in the chain in order, and records the provider id that answered.

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

- **Local container:** `docker compose up --build` runs the console on `127.0.0.1:8000`, described under Deployment and operations.
- **Hosted demo (not built):** a public server with TLS, an access code and a spend limit. Dropped; see Decisions.
- **Finom's own infrastructure:** import the package and set the model base URL, described under Deployment and operations.

**Local testing**

The whole system runs on a laptop with no API keys. The shipped routing is the fake provider only.

- `uv sync` installs it, `uv run pytest` runs the tests, `uv run python -m triage_agent serve` starts the console, `uv run python -m triage_agent classify "<text>"` prints the masked text and the classifier result from the active chain, for a one-off live check, and `uv run python -m triage_agent eval --split dev` runs the evaluation set offline with the fake provider.
- A **fake LLM provider** classifies by keywords or returns scripted answers, so tests are fast and repeatable.
- Adapter tests use httpx's mock transport, so no test makes a real HTTP call.
- The console loads the fixture tickets and templates into SQLite, in memory by default.
- Console tests use FastAPI's in-process test client, so they also run with the network blocked.
- `docker compose up --build` builds the image and starts the console; `http://127.0.0.1:8000/health` shows whether it is up and which models are active.

**Mandatory QA tests**

These run in CI on every push to `main` and on every pull request, and locally with `uv run pytest`.

| Test | What it proves | State |
| --- | --- | --- |
| Approval gate | Nothing is sent without a recorded approval | Built |
| Safety rails | A config that allows automatic reply for a locked class is rejected, even after renaming it | Built |
| Rules override the LLM | A fraud message is escalated even when the fake model says otherwise, and the model is not called | Built |
| PII masking and leak check | Masking round-trips, a masking error blocks every provider call, and seeded IBANs, cards, emails and phone numbers never reach a model payload, an audit row or a log line, including on error paths | Built, plus the leak rate in `eval` |
| Prompt-injection set | Hostile messages change neither the class nor the actions taken | Built: unit tests plus 8 injection tickets in the eval set |
| Evaluation regression | The fake-provider run stays at or above `eval/baseline.json`; leak rate 0, no reply sent and injection pass rate 1.0 are enforced | Built |
| Model adapters | Request URL, headers and body, response parsing, and HTTP errors, timeouts and malformed bodies, for both adapters over mocked HTTP | Built |
| Secret scan | No API-key-shaped string (`sk-…`) in `src/`, `pack/`, `tests/`, `docs/`, `.github/`, the `Dockerfile`, `docker-compose.yml` or the README, and `.env` files are git-ignored | Built |
| Pause switch | While paused, fraud still escalates, everything else goes to a person, and the model is not called | Built |
| System page | Shows the flow, settings and rules from the loaded pack and follows a changed pack after a restart; shows whether a key variable is set but never its value; GET changes nothing; config text is escaped | Built |
| Packaging | `/health` returns ok and the provider ids without state change or secrets; compose passes the key variables by name only; the Dockerfile copies only existing paths | Built |
| Reviewer console | Scenarios land in the right lane; approve, edit, stale draft, escalate and wrong class behave and log correctly; exactly one primary button per card; trail steps link to System; GET never changes state; customer text is escaped | Built |

Shorter unit tests cover config validation, template filling (a missing field or language blocks the draft), audit-log fields and config version, the model-down path, invalid model output, deadlines, the journey trail and the no-network guard. Storage tests on Postgres are (not built). CI also runs the ruff linter. A run with the real model on demand is the `classify` command.

**Cautions**

- Do not copy Finom's real UI, logo or help-center text. Use a neutral design with their name and colors, label it as an unofficial demo with synthetic data, and write your own templates.
- Publish the core and the generic pack publicly. Share the Finom pack with them directly.

## Decisions

- **Confidence** is self-reported by the model, with a threshold of 0.7. It is not calibrated, and in the live run the threshold had little effect.
- **Deadlines** are ISO 8601 durations (days, hours, minutes) in calendar time, UTC, counted from `received_at`.
- **A two-issue ticket gets one label.** If the model is unsure between topics, low confidence sends it to a person.
- **`config_version`** is the first 12 characters of a sha256 over all pack files, with line endings normalized.
- **Pause lets only escalate rules through.** The LLM is not called while paused.
- **The console is plain server-rendered forms:** no JavaScript, one fixed reviewer id.
- **Masking over-masks and needs no checksum.** A mistyped IBAN is masked too; a harmless number may be masked as well.
- **Adapters only send text.** The prompt, the parsing and the validation live once in the core, so a new provider is a small HTTP adapter.
- **The Anthropic adapter sends no `temperature`,** because API version `2026-10-21` rejects sampling parameters. It is tested with mocked HTTP only.
- **Coverage counts missing-field and other-language tickets,** so it cannot reach 1.0. State accuracy shows whether tickets ended where expected.
- **The baseline comes from the fake provider.** Tests compare against it and never write it; `eval --write-baseline` updates it on purpose.
- **The System page is read-only.** It shows the loaded config and changes nothing. There is no editing UI: config belongs in git with review before merge, and a restart plus a new config version makes every change traceable.
- **Rules only escalate.** The `route` action was never used and was removed; a pack that still uses it is rejected.
- **The `demo` command is gone.** The console replaced it; the fixture outcomes are still checked by a test that runs the pipeline directly.
- **The hosted demo is dropped.** A public demo needs hosting, TLS, an access code and spend limits. A recorded demo plus docker-compose serve the pitch better.
- **Dev for tuning, test once.** Prompt and config changes are judged on the dev split; the test split is run once after dev is settled.

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
