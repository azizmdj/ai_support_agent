# Support Triage Agent: Project Document

Oct 31, 2026 · @Aziz M

## Status

| Phase | State | What it contains |
| --- | --- | --- |
| 1 | Built | Core pipeline, rules, approval gate, audit log, config validator |
| 2 | Built | Reviewer console (sidebar queue, collapsed Steps trail, light and dark mode), New message screen with scenario buttons and a free-text composer, pause switch, escalate, wrong class |
| 3a | Built | Model providers (OpenAI-compatible and Anthropic adapters), PII masking, fallback chain, `classify` command |
| 3b | Built | 100 synthetic tickets with seeded fake PII, the `eval` command and metrics including PII leak rate and injection resistance, a live run with gpt-4o-mini |
| 4 | Built | Dockerfile, docker-compose, health endpoint, CI with ruff and pytest, full README. The hosted demo is dropped (see Decisions) |
| 5 | Built | Clean-up (the `demo` command and the unused `route` rule action are gone), a read-only System page, a simpler decision card, model hyperparameters in `pack/models.yaml`. The test suite passes in CI |
| 6 | Built | Story 0: three tabs (Setup, Review, Monitor), an AI decision box on every card, collapsible one-line queue lanes with a Done lane, an honest missing-field card, plain wording, and a seed with only one overdue case |
| 7 | Built | Eval evidence: per-ticket records, rule vs model split, repeated runs (`--repeat`), Wilson intervals, a confidence table, a stricter output parser, a prompt snapshot test, `export-corrections`, and live runs with two models (gpt-4o-mini and claude-haiku-5-5) |

## Problem

A bank receives hundreds to thousands of customer messages a day, and every one must be sorted, routed and answered correctly and quickly. The mix includes fraud reports, blocked cards, pending transfers, fee questions, complaints and general questions.

Three kinds of mistake are costly:

- A fraud report that waits in the general queue can cost the customer money.
- A complaint that misses its response deadline is a regulatory problem.
- A wrong answer about fees or limits creates disputes.

Sorting and first-draft answers are repetitive work, which makes them a good fit for automation, as long as the risky cases stay with people.

## Solution in one view

Code handles what must be exact, an LLM handles language, and a human decides anything with consequences. The agent speeds up sorting and drafting and never acts alone.

Technically it is a guarded AI workflow with one LLM classification call per message: no agent loop, no tools and no memory. The model returns a class, a sub-type and a confidence; code decides everything that follows.

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
- A reviewer console: three server-rendered tabs, Setup, Review and Monitor, with plain forms
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

**Why there is no ReAct or tool loop.** One call per message keeps the work bounded: a fixed cost, a fixed latency and no path that runs longer than planned. Every decision is auditable, because the model's only output is a class and a confidence that code checks against the taxonomy and the threshold. The model never chooses an action: routing, template choice, field filling and sending are code and the reviewer, so a manipulated model can at most pick a wrong class, which the locked classes and the approval gate contain.

## Adapting it to Finom

Finom can take the code and change behavior through configuration files and small adapters, without touching the agent logic.

| What Finom wants to change | How it works |
| --- | --- |
| Add or edit message classes | Classes live in one `taxonomy.yaml`: name, description, priority, target team, deadline, risk flag and reply mode (template or no draft). A new class is a new block in that file. |
| Edit reply templates | Approved reply templates are Markdown files with YAML front-matter, one folder per language (`templates/en`, `templates/de`). Finom imports its existing templates, and every case and audit entry names the template it used. |
| Add their own rules | `rules.yaml` holds keyword and regex rules. The only action is `escalate`: the ticket goes straight to a person, with no draft. Rules run before the LLM and always win. When a rule fires, the LLM is not called. |
| Connect their ticket system | A `TicketSource` interface with `fetch_new` and `send_reply`. We ship an in-memory mock; Finom writes a small adapter for the real system. |
| Use their own models | A `TextModel` interface with one method, `complete(system, user)`, that returns text. Two adapters ship: `openai_compatible`, which also covers hosted gateways and local servers such as Ollama and vLLM through a base URL, and `anthropic`. Adapters only move text; the prompt, the parsing and the validation live once in the core (`LLMClassifier`). `pack/models.yaml` lists the providers and an ordered fallback chain, and `TRIAGE_PROVIDER` overrides the chain for one run. Each provider can set `temperature` (0 to 2, OpenAI-compatible only), `max_tokens` (1 to 4096) and `timeout_seconds` (1 to 120); unset values keep the adapter defaults (temperature 0, 300 tokens, 20 s for OpenAI-compatible; no temperature, 1024 tokens, 20 s for Anthropic). A deterministic fake provider is the default. All HTTP behavior is tested with mocked HTTP. Both adapters have also run the full evaluation against the live APIs (gpt-4o-mini and claude-haiku-5-5, 2026-11-18); the Anthropic adapter needed no change. Model ids are pinned to a dated snapshot where the provider offers one (`gpt-4o-mini-2024-07-18`); `claude-haiku-5-5` has no dated snapshot, so its id stays the alias. |
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
- **No message text in the audit log.** Audit rows hold only ids, class names, confidence, rule id, template id, the provider id that answered, hashes and short codes. The code rejects any other payload field. When a rule fires, the row names the rule and the position of the keyword in `pack/rules.yaml`, never the keyword itself, because a keyword that fired also occurs in the message. The case store keeps the customer message text, the keyword and the model's own short explanation (at most 300 characters, shown as unverified) so the reviewer can read them; the audit log never does. A configurable retention period (planned, later).
- **Check for leaks.** A test seeds tickets with fake IBANs, card numbers, emails and phone numbers and checks that none of them appears in any model payload, audit row or log line, including the error paths. The `eval` command also measures the PII leak rate at the model boundary: every payload sent to a model is recorded and checked for the seeded values.
- **Route by sensitivity (planned, later).** Each class sets which models may see it.

Detection is pattern based and deliberately over-masks. It needs no checksum, so a mistyped IBAN or card number is masked too, and some harmless numbers may be masked as well. Phone numbers must start with + or 0 and have at least 7 digits. Names and free-text addresses are not detected.

Automatic detection misses things, especially names and free-text addresses, and masked text can still identify a person under DSGVO. Masking reduces risk but does not remove it. **Hosting:** for a hosted model, a bank would use an EU endpoint, a data processing agreement and zero-retention settings with the provider; alternatively it runs a local model through the OpenAI-compatible `base_url`, so messages never leave its network.

## Product design

The reviewer console is the product: customers only see the reply, so the reviewer's screen should make a routine ticket one action. It tells one story in three tabs, in this order: **Setup** (understand the system), **Review** (the daily work) and **Monitor** (the overview for a team lead). Every button on a decision card calls an action in the approval gate. Setup and Monitor have no buttons.

**Who uses it**

- **Support reviewer** (most of the time): approves, edits, escalates or corrects the class, and takes fraud and complaint cases.
- **Team lead** (occasionally): checks Monitor for open, overdue and done cases and the latest evaluation; adds classes and templates by editing the config files, then checks Setup and runs `eval` to compare accuracy before and after a change.
- **Customer:** receives faster, consistent replies in the usual channel. Code appends a short note to every draft saying the reply was prepared with AI assistance and reviewed by the team.

**Header**

The header shows the app name with "Unofficial demo, synthetic data" and the links Setup, Review and Monitor. Nothing else. The model, the config version and the pause switch sit in the Review sidebar.

**Setup: read-only (`/system`)**

Everything on Setup is derived from the loaded config at request time, so an edit to a pack file shows up after a restart. The page has no sidebar and no form, a column of about 960px, and a one-line status with the model, config version and pause state. Each block is collapsed and ends with "Change this in pack/<file>.yaml, then restart."

- **How it works** (open): three lines. Rules first; the AI only classifies; a person approves.
- **The six steps of every message:** a table, always visible: who does each step, what it does, input and output, and which file or setting controls it. Each step has an anchor (`#received`, `#rules`, `#classify`, `#template`, `#review`, `#reply`) that the trail on each card links to.
- **AI model and threshold** (`#settings`): the confidence needed for a draft, the pause state, the reviewer id, the config version, and the model chain with kind, model, host, temperature, maximum answer length, timeout and the key variable name with "set" or "not set". A key value is never shown.
- **Classes:** name, description (the class list in the AI prompt), priority, team, risk ("locked: always a person") and reply ("approved template" or "no draft").
- **Rules and their keywords:** each rule with its target class, languages and every keyword and pattern.
- **The exact AI prompt:** the system and user prompt, built by the same functions the classifier uses, with a placeholder for the message. The page calls no model. The test model does not use a prompt.
- **Approved reply templates:** every template file with its id, language, required fields and text.
- **Template fields and the AI-assistance note:** the allowed ticket fields and the note added to every draft, per language.
- **Deadlines:** per class, in words ("1 hour", "15 days").
- **Explain:** three collapsed sections: Business (the three costly mistakes and what prevents each), Math (the decision rule and metric definitions, see "How to read the numbers") and Engineering (interfaces, case states, audit, fail-closed paths, how to add a class or a model).

**Review: one screen, one queue**

&#91;embedded content: reviewer console · one card per ticket\]

The page has a slim sidebar on the left and a centered column of about 720px. The sidebar starts with a "New message" link to the home screen, then lists the queue in four collapsible lanes (HTML `details`, no JavaScript), each with its count in the title. Each item is one short line: ticket id, class and due time, for example "T03 · fraud report · 30m late" or "T08 · card blocked · due in 3h". Only the queue scrolls, so the footer stays visible: the model (linked to the settings on Setup; the offline fake shows as "test model (no real AI)"), the config version, the pause state with its switch, and one line on what Pause does.

- **Urgent** (open): escalated cases, sorted by deadline.
- **Ready to approve** (open): drafted cases, sorted by deadline.
- **Needs a person** (open): unclear or low-confidence messages, model errors, paused tickets and drafts blocked by a missing field or language, sorted by deadline.
- **Done** (collapsed): sent and taken cases, newest first, each with its outcome ("sent" or "taken"). It opens when a closed case is shown.

"New message" (`/new`) is a home screen with no card: one line, a text box "Send a test message..." with a language select and a Send button, and four small buttons below it (Routine question, Fraud report, Complaint, Hostile message). Each creates a new ticket, runs it through the pipeline and opens its card. Each scenario button rotates between an English and a German variant. A quiet line under the box says: synthetic data only, names and addresses are not masked. The text box appears only on this screen, never on a case page, so it cannot be mistaken for a reply to the customer.

Each ticket is one decision card, in this order: a line with the ticket id, class and due time; the customer message; the **AI decision** box; a collapsed "How this was decided" section; the draft if there is one; exactly one primary button; and the collapsed secondary actions.

The **AI decision** box is always visible and compact, so the draft and the main button fit on a laptop screen without scrolling. When the model was called it shows a one-line summary; the predicted class, the confidence next to the threshold ("0.90, needs 0.70") and which model answered on one line; the masked text the model saw (the first 160 characters; the full text is under "How this was decided"); and the model's own explanation marked "unverified". A low-confidence case keeps its predicted class here, although its class is `unclear`. When the model was confident but the draft was blocked, the summary says so: "The AI picked a class with enough confidence, but a required detail is missing (card_last4), so no draft was made." When no model was called it says why in one line: the rule and the keyword or pattern from `pack/rules.yaml` that fired, the pause switch, or a masking failure.

| Card | Primary button | Secondary actions |
| --- | --- | --- |
| Drafted | **Approve and send** (the draft is editable; a changed text is logged as an edit, then approved) | "Change class or escalate": Wrong class, Escalate to a person |
| Needs a person | **Set class and draft** (the class select starts on "Choose a class..."; submitting without a class is rejected with a "Not done:" banner and changes nothing) | "Change class or escalate": Escalate to a person |
| Needs a person, draft blocked by a missing field or language | **Escalate to a person**, under the note "A required detail is missing, so no draft can be made. Ask the customer or escalate." | "Change class": Wrong class (a different class may use a template that needs fewer fields) |
| Escalated (fraud, complaint) | **Take case** | none |

- After any action the next ticket opens automatically: Urgent first, then Ready, then Needs a person. A one-line banner confirms the action, such as "Done: Reply sent via mock ticket system."
- A class change says "moved to the lane for its new class" only when the case really moved. If it stays, the same case opens again with "A new draft was made" or "no draft could be made. The case stays in Needs a person."
- Sent and taken cases leave the open lanes and appear under Done.
- A stale form (the draft changed since the page loaded) is rejected with a clear message, and nothing is sent.
- Opening Review shows the most urgent case. An empty queue shows the New message screen, starting with "Queue is empty."
- On a phone the sidebar sits above the card, and the queue is a section "Queue (n)" that is open by default and can be folded away.

**How this was decided**

This section on each card is collapsed by default. Its summary carries the reason, for example "How this was decided · rule fraud_keywords". Opened, it lists the six steps, one line each, and each step name links to its row on Setup.

| Step | Actor | Example result |
| --- | --- | --- |
| 1. Message received | Customer | received time |
| 2. Rules | Code | "rule fraud_keywords fired on the keyword "betrug"", "no rule matched", "held: paused" |
| 3. Classify | AI | "fee_question 0.90 via openai_main", "skipped: rule fired", "failed: model error", "failed: masking failed, no model call" |
| 4. Template | Code | template id, "no draft: missing card_last4", or "no draft: rule fraud_keywords fired" |
| 5. Review | Reviewer | "approved by …", "taken by …", or waiting |
| 6. Reply | Code | "sent, with AI-assistance note", "not sent yet", "no reply: handled by a person" |

Each step has a written status: Done, Skipped, Your turn or Pending. "Your turn" appears once per card, on the step that waits for the reviewer. The trail is built only from the case, its audit rows and the loaded config.

Below the steps, the same section shows the case facts (state, class, priority, team, due time) and "What each step saw and produced": one row per step with its input, output and the config used. For Classify the input is the masked message, computed from the stored ticket when the page loads and never stored. The raw audit rows sit in a nested, collapsed "Audit log".

**Monitor: read-only (`/monitor`)**

One page for a team lead, with plain numbers and no charts:

- Counts by state: urgent (escalated), ready to approve, needs a person, sent and taken, plus the number of open cases past their deadline, with a link to each. A case left in `new` (triage stopped before routing) adds a "Not routed yet (new)" count, shown only when there is one.
- Cases by class, open and done. Open and done come from one definition in `models.py`: a state with a way out in the transition table is open, including `new`; `sent` and `taken` are done.
- The pause state, the model and the live config version.
- The latest evaluation report from `eval/results/` (the newest run date; on the same date the test split wins; on the same date and split, the provider listed first in `pack/models.yaml` wins): date, model, split, fraud and complaint tickets drafted, personal-data leaks to the model, classes correct, and the config version it ran on. If that version differs from the live one, a note says "Eval ran on an older config". A run with the test model is labeled a regression check, not a result. Without a report, the page says so.
- The 20 most recent audit rows across all cases, newest first, with only the fields the audit log allows.

**Trust features**

- The AI decision box on every card: what the model saw, what it predicted, how sure it was against the threshold, or why no model was called.
- The trail and the step table, so a first-time visitor sees who did what with which input.
- Wrong class is one click and doubles as labeled feedback: a `class_corrected` audit row with the old and new class. `export-corrections` turns these rows into masked candidate eval tickets for a person to review (see "Learning from reviewer corrections").
- A pause switch in the sidebar footer, with one line on what it does. While it is on, rules still run and fraud and complaint keywords still escalate, the LLM is not called, and every other new message goes to Needs a person with the reason "paused". Cases already in the queue are not changed, and reviewers can still approve, escalate and take cases. Switching it writes a `pause_on` or `pause_off` audit row.
- A typed test message is checked before anything is created; it never appears in an audit row or a URL.
- State is always written as text, never shown by color alone. System fonts, light and dark mode from the system setting, no icons, no animation, no JavaScript.

**Demo experience**

`docker compose up --build` or `uv run python -m triage_agent serve` starts the console at http://127.0.0.1:8000. On first start with an empty database it loads the 15 fixture tickets. The newest arrives "just now", and the ages of the older ones are scaled to a quarter, so the first screen shows cases that are due soon and exactly one clearly late fraud report (T03). Only the seeded arrival times change; the deadline rules are the same as for any ticket.

A visitor opens Setup to read "How it works", switches to Review, opens New message, clicks a scenario button or types a short message, reads the AI decision box on the card, and opens Monitor to see the counts and the latest evaluation. That shows flexibility, banking fit and safety in two minutes.

**Left out on purpose**

- An editing UI for the config. Setup only shows the config. Classes, rules, templates and settings are edited as files, because config belongs in git with review before merge.
- JavaScript tabs or widgets. Lanes and blocks fold with plain HTML `details`.
- Charts and dashboards. Monitor shows plain counts.
- Free text in the audit log. The model's explanation is kept on the case only, and a rule keyword is logged as its position in `pack/rules.yaml`, not as text, because a keyword that fired also occurs in the customer's message.
- A customer-facing chat window, which is a different product with different risks. The New message text box is a test input for the demo, not a customer-facing chat.
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

**Tickets:** 100 tickets in `eval/tickets.jsonl`, written by a Claude agent (Claude Code) and committed: 62 in the dev split and 38 in the test split, stratified per class. Real traffic will be messier than text one model wrote in one sitting. Because a Claude model wrote the tickets, a Claude classifier may find them easier than real messages: gpt-4o-mini is the cross-family result, claude-haiku-5-5 is same-family and likely optimistic. Each ticket has a true class, an expected state and, where one applies, the expected template. About half carry seeded fake personal data, and 8 are injection attempts with a unique canary token. The 15 handwritten tickets in `tests/fixtures/tickets.jsonl` seed the console. The set includes hard cases:

- Fraud reports and complaints without any rule keyword, some with typos
- Two issues in one message (the true class is the one with the higher priority)
- Vague or very short messages
- Out-of-scope requests such as tax or legal advice
- Mixed German and English, rude but legitimate messages, and a few French or Turkish messages, which have no template
- Prompt-injection attempts, for example a message telling the agent to ignore its instructions

All personal data is fake, such as made-up names, fictional IBANs (some mistyped), test card numbers, example.com emails and 555-style phone numbers.

**Metrics**

`uv run python -m triage_agent eval [--split dev|test|all] [--provider IDS] [--repeat N]` runs every ticket through the real pipeline on a fresh in-memory store and never approves anything. It prints a table and writes two files to `eval/results/`:

- `<provider>-<split>-<date>.md`: the provider and model, the config version, the threshold, the number of runs, all metrics, the Wilson intervals, the rule vs model split, the stability block, the confidence table, latency, and a list of misses by ticket id.
- `<provider>-<split>-<date>-tickets.jsonl`: one line per ticket and run with `run`, `ticket_id`, `true_class`, `expected_state`, `decided_by` (rule, model or none), `rule_id`, `predicted_class`, `model_label` (the model's own label, kept when low confidence sent the case to `unclear`), `subtype`, `confidence`, `state`, `template_id`, `model_latency_ms` and `model_error` (a short code such as `invalid JSON in model output`).

Neither file contains message text, the model's explanation, PII values or keys; a test checks both. Token counts are not recorded, because the adapters do not expose them.

With `--repeat N` the set runs N times on the same config. The main metrics are always run 1, so a repeated report compares with a single-run one; runs 2 to N call the model again for every ticket and feed only the stability block.

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
| Rule vs model split | Fraud and complaint tickets caught by a rule (no model call) versus keyword-free ones the model had to classify, each keyword-free ticket with its class, state and "escalated in k of N runs" |
| Stability (with `--repeat`) | Class accuracy min, mean and max over runs; tickets whose predicted class or final state changed; risky tickets drafted in any run; model errors per run |
| Wilson 95% interval | For class correct, state correct, risky drafted and PII leaked (see below) |
| Confidence table | Model-called tickets of run 1, one row per distinct reported confidence, with tickets and how many had the right label |

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

**Small samples.** Each rate comes with a Wilson 95% interval: the range of true rates that fit the observed k of n. n always counts distinct tickets, never runs: a ticket seen five times is still one ticket. Class and state accuracy use run 1. Risky drafted and PII leaked count a ticket once if it happened in any run, so repeats can only make them worse. The PII leak denominator is the PII tickets that reached a model (28 of 32 in dev, 14 of 19 in test); the others hit a rule first and were never sent.

So "0 drafted" is evidence, not proof: 0 of 11 test fraud and complaint tickets still allows a true rate up to 26%, and 0 of all 28 up to 12%. The dev split was used to tune the prompt, so the test-only figure is the clean one. The tickets are synthetic and written in one style, so the intervals describe this set, not real traffic.

**Non-LLM baseline.** The keyword fake (`FakeLLM`, behind the same pipeline) is the baseline without a language model: class accuracy 0.613 on dev and 0.605 on test, and it drafts two keyword-free fraud reports in dev (`eval/baseline.json`). It is a keyword list, not a trained classifier.

**Results**

Two models on the same prompt, class descriptions, rules and threshold (0.7), run on 2026-11-18 on config version `40b28e461762`: gpt-4o-mini (`openai_main`, pinned to `gpt-4o-mini-2024-07-18`, temperature 0) with 5 runs per split, and claude-haiku-5-5 (`anthropic_haiku`, no temperature, low effort) with 3 runs per split. The numbers come from `eval/results/*-2026-11-18.md`; the per-ticket lines are in the matching `-tickets.jsonl` files. Intervals are Wilson 95%.

| Measure | gpt-4o-mini dev (62) | gpt-4o-mini test (38) | Haiku dev (62) | Haiku test (38) |
| --- | --- | --- | --- | --- |
| Class correct, run 1 | 57 (0.919; 0.825 to 0.965) | 37 (0.974; 0.865 to 0.995) | 60 (0.968; 0.890 to 0.991) | 34 (0.895; 0.759 to 0.958) |
| State correct, run 1 | 59 (0.952; 0.867 to 0.983) | 38 (1.000; 0.908 to 1.000) | 59 (0.952; 0.867 to 0.983) | 34 (0.895; 0.759 to 0.958) |
| Fraud and complaint drafted, any run | 0 of 17 (0 to 0.184) | 0 of 11 (0 to 0.259) | 0 of 17 (0 to 0.184) | 0 of 11 (0 to 0.259) |
| Caught by a rule / left to the model | 8 / 9 | 6 / 5 | 8 / 9 | 6 / 5 |
| Keyword-free ones escalated by the model | 9 of 9 in all 5 runs | 5 of 5 in all 5 runs | 9 of 9 in all 3 runs | 5 of 5 in all 3 runs |
| PII leaked, of tickets that reached a model, any run | 0 of 28 (0 to 0.121) | 0 of 14 (0 to 0.215) | 0 of 28 (0 to 0.121) | 0 of 14 (0 to 0.215) |
| Wrong draft rate, run 1 | 0.086 | 0.056 | 0.030 | 0.000 |
| Coverage, run 1 | 0.946 | 0.783 | 0.892 | 0.609 |
| Template accuracy, run 1 | 0.865 | 0.952 | 0.892 | 0.810 |
| Class accuracy min / mean / max over runs | 0.919 / 0.926 / 0.935 | 0.947 / 0.968 / 0.974 | 0.968 / 0.968 / 0.968 | 0.895 / 0.895 / 0.895 |
| Tickets whose class changed between runs | 1 (GQ09) | 1 (PT13) | 0 | 0 |
| Model errors, all runs | 0 | 0 | 0 | 1 (FQ13 in run 2: invalid output, went to a person) |
| Injection tickets passed, run 1 | 3 of 3 | 5 of 5 | 2 of 3 (GQ11 went to a person) | 5 of 5 |
| Model latency median / p90, run 1 | 1369 / 1778 ms | 1175 / 1351 ms | 1048 / 1219 ms | 916 / 1052 ms |
| Replies sent | 0 | 0 | 0 | 0 |

For comparison, the keyword fake without a language model scores class accuracy 0.613 (dev) and 0.605 (test).

**What the numbers say**

- **Safety held in every run.** Across 16 runs (10 with gpt-4o-mini, 6 with Haiku), no fraud or complaint ticket got a draft. Rules caught 14 of the 28 risky tickets before any model call. The other 14 have no rule keyword, and both models escalated all 14 in every run. Because n counts tickets, the honest upper bound stays 26% on the 11 test tickets and 12% on all 28, however many runs pass.
- **The two models fail differently.** gpt-4o-mini drafts the wrong class: three fee questions labeled `general_question` (FQ04, FQ06, FQ13) got a draft that a reviewer must catch. Haiku sends more to a person instead: wrong draft rate 0.030 on dev and 0 on test, but test coverage 0.609. It also over-escalated one rude fee question as a complaint (FQ05).
- **Same-family caution.** The tickets were written by a Claude agent, so the Haiku numbers are likely optimistic; gpt-4o-mini is the cross-family result. Haiku did not score higher on test (34 vs 37 of 38), which does not remove the concern.
- **Run-to-run variation, measured.** At temperature 0, gpt-4o-mini changed the class of exactly one ticket per split over 5 runs: GQ09 on dev and PT13 on test, the same two tickets that differed between the 2026-11-02 and 2026-11-08 runs. Class accuracy moved within 0.919 to 0.935 on dev and 0.947 to 0.974 on test. Haiku changed no class in 3 runs.
- **Invalid output is rare and fails closed.** In its 3 runs, Haiku returned one answer that was not valid JSON; the case went to a person with a `model_error` row. gpt-4o-mini returned none in 5 runs.

**Reported confidence**

The model's confidence is self-reported, not a calibrated probability. Model-called tickets of run 1, by reported value:

| Confidence | gpt-4o-mini dev | gpt-4o-mini test | Haiku dev | Haiku test |
| --- | --- | --- | --- | --- |
| 0.95 | 8 (8 right) | 5 (5) | 6 (6) | 5 (5) |
| 0.93 | | | 13 (13) | 8 (8) |
| 0.92 | | | 2 (2) | |
| 0.90 | 35 (33) | 22 (21) | 16 (16) | 11 (11) |
| 0.85 | | | 3 (3) | 1 (1) |
| 0.80 | 2 (1) | 1 (1) | | 1 (0) |
| 0.75 | | | 3 (3) | 1 (1) |
| 0.70 | | | 1 (1) | |
| 0.60 | | | 3 (2) | 4 (2) |
| 0.50 | 7 (5) | 3 (3) | 3 (2) | |
| 0.40 | | | 1 (1) | |
| 0.30 | | | 3 (3) | 1 (1) |
| 0.10 | 2 (2) | 1 (1) | | |

- gpt-4o-mini uses five values. 43 of its 54 dev model-called tickets are at 0.9 or higher, and two of its dev errors sit at 0.90. No ticket reports a value between 0.5 and 0.8, so its dev threshold sweep is flat from 0.5 to 0.8 (coverage 0.946, wrong draft rate 0.086) and the threshold is not a tuned control for it.
- Haiku spreads its answers from 0.30 to 0.95, and its dev sweep is not flat: at 0.8 the wrong draft rate drops from 0.030 to 0 while coverage drops from 0.892 to 0.811. The threshold stays 0.7: this is one model on 62 dev tickets, and the threshold was not tuned.

**Injection tickets.** There are 8. Two (FR15, CO13) contain a fraud or complaint keyword, so a rule escalates them before any model call; 6 reach the model. The canary cannot appear in a draft by construction, because replies are approved templates filled with ticket metadata and the model writes no text that is sent. The measured part is whether the class and state stay right: gpt-4o-mini kept all 6, Haiku kept 5 and sent GQ11 to a person, a failure in the safe direction. Six tickets are too few for a rate.

**Why test coverage is at most 0.783.** The test split has 23 in-scope, non-risky tickets. 18 expect a draft and 5 expect a person: 3 have a missing field (CB03, CB10, PT05) and 2 are in a language without templates (CB05 in Turkish, CB15 in French). gpt-4o-mini drafted all 18 in run 1 (0.783). Haiku drafted 14 (0.609): it labeled PT13 and FQ13 `unclear`, escalated FQ05, and picked the PIN sub-type for CB13, whose template needs a card number the ticket does not have.

**Earlier runs.** `eval/results/` keeps the gpt-4o-mini reports of 2026-11-02 (configs `a74e7a29655e` with the original prompt, then `cb16d991171f` after one calibration change to the prompt and two class descriptions) and 2026-11-08 (`5c03d5c522f3`). The prompt and descriptions have not changed since `cb16d991171f`; later config versions come from comments, a model pin and a new provider entry. The 2026-11-08 test run scored 36 of 38 against 37 of 38 earlier; the repeated runs above show that the difference is PT13, which changes class between runs on the same config.

**Process**

The dev split was used for tuning. The test split was first run after dev was settled and has been rerun only to measure new config versions, never to tune. The fake provider is a regression guard only: its numbers are not results. It has a known gap: it drafts two keyword-free fraud reports in dev. A strict expected-failure test records the target of 0, so it fails loudly once the gap is closed.

**Known limits**

- The tickets are synthetic and written by a Claude agent. Real messages will be messier.
- The sets are small. One ticket moves a metric by about 1.6 points on dev and 2.6 points on test.
- Only structured PII (IBAN, card, email, phone) is measured. Names and addresses are not.
- The model's explanation is stored on the case only, never in the audit log, and is shown as unverified. It may repeat a customer's name, because names are not masked.
- The calibration prompt names tax or legal advice as an example, which is also an eval category.
- A fraud message without a rule keyword that the model labels as a routine class would still get a draft. The safeguard is the person who approves every reply, plus the result above (0 drafted in 16 runs).
- **A wrong class can bypass the missing-field guard.** FQ06 is a fee question without a plan name, so its template cannot be filled and it should go to a person. gpt-4o-mini labeled it `general_question`, whose template needs only the customer's name, so it got a draft. The reviewer is the control.
- Two models, one prompt. No few-shot or class-description ablation has been run.
- Token counts and cost per ticket are not recorded.

**Learning from reviewer corrections.** No model is trained. A reviewer's "Wrong class" writes a `class_corrected` row. `uv run python -m triage_agent export-corrections --db <case store>` joins those rows with the stored ticket text, masks it, and writes `eval/candidates.jsonl`: one line per corrected case with `true_class` set to the reviewer's class, `needs_review: true` and no split. A person reads each line (names and addresses are not masked), adds the split and the expected state, and only then moves it into `eval/tickets.jsonl`. Prompt or description changes are then judged on dev as before.

Finom can add its own labeled tickets, rerun `eval`, and see accuracy before trusting the agent with anything.

## Deployment and operations

The core is a plain Python package with SQLite. It runs on a laptop with no network, in a container, and Finom can import it into its own services.

- **Packaging:** an installable Python package (`pyproject.toml`, managed with `uv`).
- **Docker:** a `Dockerfile` on `python:3.13-slim` installs the locked dependencies with `uv sync --frozen --no-dev` in their own layer, then copies the code, the pack, the eval set and the console fixture tickets. It runs as a non-root user, exposes port 8000 and starts `serve --host 0.0.0.0`. A `HEALTHCHECK` calls `/health` with Python's `urllib`. `.dockerignore` keeps `.git`, `.venv`, caches, `.env` files and databases out of the image. The committed eval reports in `eval/results/` are part of the image, so the Monitor tab in the container shows the latest one. A new report needs a rebuild (`docker compose up --build`).
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
src/triage_agent/web/        reviewer console: FastAPI app, journey trail and AI decision box, Setup and Monitor pages, Jinja templates, one CSS file
pack/                        taxonomy, rules, settings, templates, scenarios, models (providers and routing)
tests/                       unit, adapter, masking and console tests, fixture tickets
README.md
pyproject.toml
eval/                        tickets.jsonl (100 tickets), baseline.json (fake-provider baseline), results/ (reports and per-ticket records), candidates.jsonl (exported corrections, when written)
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
6. **Story 0:** the Setup, Review and Monitor tabs, the AI decision box, one-line collapsible lanes with a Done lane, the missing-field card, plain wording and the seed change. (built, phase 6)

Keep Finom-specific wording in the pack files and not in the core, so a second pack is a copy. One exception today: the fake LLM's keyword list lives in the core.

**Storage and integrations**

- The default is basic: SQLite for cases, tickets, the pause flag and the audit log, and plain template files loaded by id.
- Both sit behind interfaces. A Postgres case store (not built). A template source that points at a bank's existing template system (planned, later).
- A contract test that runs the same suite on SQLite and Postgres (not built).
- Messages are processed as they arrive. There is no queue and no search index, because the class decides the template.

**Pipeline**

The pipeline is plain Python: rules, classify, pick template, fill template. It gets no ticket source, so it cannot send anything. Case state lives in the database. A ticket id that already has a case is refused with `DuplicateTicket`: the case, its ticket and its audit rows stay as they are. The console then shows "Nothing was created" instead of an error page. While the pause switch is on, rules still escalate; the LLM is not called and every other ticket goes to a person. The classify step masks the message, tries the providers in the chain in order, and records the provider id that answered.

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

`sent` and `taken` are final. A test drives every pair in the table through its real code path and checks that every other pair is rejected.

The steps map directly to graph nodes if Finom uses an orchestrator.

**Deployment**

- **Local container:** `docker compose up --build` runs the console on `127.0.0.1:8000`, described under Deployment and operations.
- **Hosted demo (not built):** a public server with TLS, an access code and a spend limit. Dropped; see Decisions.
- **Finom's own infrastructure:** import the package and set the model base URL, described under Deployment and operations.

**Local testing**

The whole system runs on a laptop with no API keys. The shipped routing is the fake provider only.

- `uv sync` installs it, `uv run pytest` runs the tests, `uv run python -m triage_agent serve` starts the console, `uv run python -m triage_agent classify "<text>"` prints the masked text and the classifier result from the active chain, for a one-off live check, `uv run python -m triage_agent eval --split dev` runs the evaluation set offline with the fake provider (add `--provider openai_main --repeat 5` for a live, repeated run), and `uv run python -m triage_agent export-corrections --db <file>` writes reviewer corrections to `eval/candidates.jsonl` for human review.
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
| Model output and prompt guard | The parser accepts only a JSON number as confidence (a bool, a numeric string, null or a list is rejected and goes to a person), ignores extra keys and tolerates code fences; a label in the wrong case ("Fee_Question") goes to `unclear`; a snapshot test fixes the full system prompt for the shipped taxonomy (`tests/snapshots/`, updated on purpose with `uv run pytest --update-snapshots`) | Built |
| Eval evidence | Wilson interval values, rule vs model split counts, decided-by and per-ticket record fields, repeated runs and the stability block, the confidence table, n counted per ticket and not per run, error and latency tracing, per-ticket files without text or PII, and `export-corrections` masking and marking for review | Built |
| Secret scan | No API-key-shaped string (`sk-…`) in `src/`, `pack/`, `tests/`, `docs/`, `.github/`, the `Dockerfile`, `docker-compose.yml` or the README, and `.env` files are git-ignored | Built |
| Pause switch | While paused, fraud still escalates, everything else goes to a person, and the model is not called | Built |
| Setup page | Shows the flow, settings, classes with descriptions, rule keywords, templates, allowed fields and the exact prompt from the loaded pack, and follows a changed pack after a restart; shows whether a key variable is set but never its value; GET changes nothing; config text is escaped | Built |
| Monitor page | Counts by state and class, overdue cases, the latest eval report (on a tie of date and split, the provider listed first in `pack/models.yaml`) with an older-config note or a no-report note, the 20 newest audit rows without message text; GET changes nothing | Built |
| Packaging | `/health` returns ok and the provider ids without state change or secrets; compose passes the key variables by name only; the Dockerfile copies only existing paths | Built |
| Reviewer console | Scenarios land in the right lane; approve, edit, stale draft, escalate and wrong class behave and log correctly; exactly one primary button per card; a missing-field card offers Escalate; the class-change banner matches what happened; queue items are one line and sent or taken cases appear under Done; the seed starts with one overdue case; the AI decision box shows the masked text, predicted class, confidence against the threshold and the rule keyword; the card order is message, AI decision, collapsed "How this was decided", draft, primary button; trail steps link to System; the header holds no controls and the pause switch sits in the sidebar; the Needs-a-person class select starts on a placeholder that the server rejects with no state change and no audit row; typed messages land in the right lane, a bad input creates no case and no audit row, the typed text never reaches an audit row or a URL, and the composer appears only on New message; GET never changes state; customer text is escaped | Built |

Shorter unit tests cover config validation, template filling (a missing field or language blocks the draft), audit-log fields and config version, the model-down path, invalid model output, deadlines, the journey trail and the no-network guard. Storage tests on Postgres are (not built). CI also runs the ruff linter. CI makes no live model call: real-model behavior is measured by `eval` runs on demand, whose reports and per-ticket records are committed, and by the `classify` command for a one-off check.

**Cautions**

- Do not copy Finom's real UI, logo or help-center text. Use a neutral design with their name and colors, label it as an unofficial demo with synthetic data, and write your own templates.
- Publish the core and the generic pack publicly. Share the Finom pack with them directly.

## Decisions

- **One LLM call, no tool loop.** The model classifies once per message and never chooses an action; see "Why there is no ReAct or tool loop" under Architecture.
- **Confidence** is self-reported by the model, with a threshold of 0.7. It is not calibrated. gpt-4o-mini reports only five values (43 of 54 dev model-called tickets at 0.9 or higher), so its dev sweep is flat between 0.5 and 0.8; Haiku spreads its values more and its sweep moves above 0.7 (see the confidence table). The threshold has not been tuned.
- **Model ids are pinned where possible.** `gpt-4o-mini-2024-07-18` is a dated snapshot; `claude-haiku-5-5` has none, so the alias is used. A model change is a config change and changes the config version.
- **Repeated runs report run 1.** With `--repeat N` the main metrics stay those of run 1, so reports stay comparable; the other runs only feed the stability block, and intervals count tickets, never runs.
- **The parser is strict about confidence.** Only a JSON number counts; `true` would otherwise become 1.0 and a string would be coerced silently. Anything else is a model error and the case goes to a person.
- **Deadlines** are ISO 8601 durations (days, hours, minutes) in calendar time, UTC, counted from `received_at`.
- **A two-issue ticket gets one label.** If the model is unsure between topics, low confidence sends it to a person.
- **`config_version`** is the first 12 characters of a sha256 over all pack files, with line endings normalized.
- **Pause lets only escalate rules through.** The LLM is not called while paused.
- **The console is plain server-rendered forms:** no JavaScript, one fixed reviewer id, one CSS file with color variables for light and dark mode.
- **Free-text composer:** a typed test message is trimmed and must be 1 to 2,000 characters in a language the pack has (en, de); otherwise nothing is created and a banner says why. Code adds the fake name "Demo Customer" so a template can greet the customer; other fields stay missing, so a card or transfer question goes to a person. A hint says to use synthetic data only, because names are not masked.
- **Masking over-masks and needs no checksum.** A mistyped IBAN is masked too; a harmless number may be masked as well.
- **Adapters only send text.** The prompt, the parsing and the validation live once in the core, so a new provider is a small HTTP adapter.
- **The Anthropic adapter sends no `temperature`,** because API version `2026-10-21` rejects sampling parameters. It is tested with mocked HTTP and ran the full evaluation live on 2026-11-18 without a change.
- **Coverage counts missing-field and other-language tickets,** so it cannot reach 1.0. State accuracy shows whether tickets ended where expected.
- **The baseline comes from the fake provider.** Tests compare against it and never write it; `eval --write-baseline` updates it on purpose.
- **Setup and Monitor are read-only.** They show the loaded config, the case counts and the latest evaluation, and change nothing. There is no editing UI: config belongs in git with review before merge, and a restart plus a new config version makes every change traceable.
- **The model's explanation is shown, not logged.** The `reason` the model returns is kept on the case, cut to 300 characters, and shown in the AI decision box as "Model's explanation (unverified)". It is never written to the audit log or a log line and never used in a reply. It may repeat a name from the message, because names are not masked. It needed no schema change, because a case is stored as one JSON document.
- **A missing field ends at a person.** When a template field or language is missing, the card offers Escalate as the main action; a class change stays available, because another class's template may need fewer fields.
- **Rules only escalate.** The `route` action was never used and was removed; a pack that still uses it is rejected.
- **The `demo` command is gone.** The console replaced it; the fixture outcomes are still checked by a test that runs the pipeline directly.
- **The hosted demo is dropped.** A public demo needs hosting, TLS, an access code and spend limits. A recorded demo plus docker-compose serve the pitch better.
- **Dev for tuning, test once.** Prompt and config changes are judged on the dev split; the test split is run after dev is settled, and only rerun to measure a new config version, never to tune.

## Before production

The demo is a starting point. Before any real use, a bank would need to cover at least these items.

- [ ] Security review, authentication and role-based access control
- [ ] Data-protection impact assessment, legal review of data flows to model providers, and deletion on request across case store and audit log
- [ ] AI Act risk classification confirmed by the compliance team
- [ ] DORA mapping: ICT third-party register entry for each model provider, resilience testing and incident reporting
- [ ] Complaint deadlines and escalation rules confirmed per market
- [ ] Evaluation on the bank's own labeled tickets, with agreed accuracy thresholds per class, including human-written tickets so the result does not rest on text written by one model family
- [ ] Load testing at real message volume
- [ ] Pilot on real tickets with a small reviewer group before wider use
- [ ] Model governance: approved models, version pinning, and a change process
- [ ] Save the sent state before calling the ticket system, or make the send idempotent; today a failed save after sending lets a retry send twice.
- [ ] Cases stuck in `new` (after a crash in the pipeline) are visible only in Monitor; add a retry job or an alert before production.

## Sources

- [Finom LLM-readable overview (licensing, products, integrations)](https://finom.co/llms.txt)
- [Finom AI launch coverage, The Paypers (beta capabilities and planned approval-based actions)](https://thepaypers.com/fintech/news/finom-launches-ai-assistant-built-into-sme-accounts)
- [Finom MCP announcement](https://finom.co/en-de/news/finom-mcp-ask-questions-inside-claude-chatgpt/)
