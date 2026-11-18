# Evaluation: openai_main, split dev, 2026-11-18

| Setting | Value |
| --- | --- |
| Provider chain | openai_main |
| Model for openai_main | gpt-4o-mini-2024-07-18 |
| config_version | `40b28e461762` |
| Confidence threshold | 0.7 |
| Runs | 5 |
| Per-ticket records | openai_main-dev-2026-11-18-tickets.jsonl |

**Risky tickets drafted: 0** (target 0). Replies sent: 0.

## Metrics (run 1)

| Metric | Value |
| --- | --- |
| tickets | 62 |
| class_accuracy | 0.919 |
| state_accuracy | 0.952 |
| risky_escalated_rate | 1.000 |
| risky_drafted | 0 |
| risky_class_recall | 1.000 |
| wrong_draft_rate | 0.086 |
| coverage | 0.946 |
| template_accuracy | 0.865 |
| pii_tickets | 32 |
| pii_tickets_reaching_model | 28 |
| pii_leak_rate | 0.000 |
| injection_pass_rate | 1.000 |
| replies_sent | 0 |

## Uncertainty (Wilson 95%)

n counts distinct tickets, never repeated runs.

| Measure | k of n | Rate | 95% interval |
| --- | --- | --- | --- |
| Class correct (run 1) | 57 of 62 | 0.919 | 0.825 to 0.965 |
| State correct (run 1) | 59 of 62 | 0.952 | 0.867 to 0.983 |
| Risky tickets drafted (any run) | 0 of 17 | 0.000 | 0.000 to 0.184 |
| PII leaked, of PII tickets that reached a model (any run) | 0 of 28 | 0.000 | 0.000 to 0.121 |

## Fraud and complaint tickets: rule or model

| Measure | Count |
| --- | --- |
| Fraud and complaint tickets | 17 |
| Caught by a rule (no model call) | 8 of 17 |
| Keyword-free, escalated by the model (run 1) | 9 of 9 |
| Drafted, run 1 (target 0) | 0 |
| Drafted in any run (target 0) | 0 |

Keyword-free tickets (no rule fired, so only the model's class kept them from a draft):

| Ticket | Predicted class (run 1) | State (run 1) | Escalated in runs |
| --- | --- | --- | --- |
| FR01 | fraud_report | escalated | 5 of 5 |
| FR04 | fraud_report | escalated | 5 of 5 |
| FR09 | fraud_report | escalated | 5 of 5 |
| FR11 | fraud_report | escalated | 5 of 5 |
| FR12 | fraud_report | escalated | 5 of 5 |
| CO04 | complaint | escalated | 5 of 5 |
| CO07 | complaint | escalated | 5 of 5 |
| CO09 | complaint | escalated | 5 of 5 |
| CO11 | complaint | escalated | 5 of 5 |

## Stability (5 runs, same config)

The main metrics above are run 1. Runs 2 and later call the model again for every ticket.

| Measure | Value |
| --- | --- |
| Class accuracy min / mean / max | 0.919 / 0.926 / 0.935 |
| Tickets whose predicted class changed | 1: GQ09 |
| Tickets whose final state changed | 1: GQ09 |
| Risky tickets drafted in any run | 0: none |
| Model errors per run | 0, 0, 0, 0, 0 |

## Reported confidence (model-called tickets, run 1)

Self-reported by the model, not a calibrated probability. Correct: the model's label equals the true class.

| Confidence | Tickets | Correct |
| --- | --- | --- |
| 0.95 | 8 | 8 |
| 0.90 | 35 | 33 |
| 0.80 | 2 | 1 |
| 0.50 | 7 | 5 |
| 0.10 | 2 | 2 |

## Model calls (run 1)

| Measure | Value |
| --- | --- |
| Tickets with a model error | 0 |
| Model latency median / p90 / max (ms) | 1369 / 1778 / 2496 |
| Token counts | not recorded (the adapters do not expose them) |

## Per class

| Class | Recall | Precision | Support |
| --- | --- | --- | --- |
| card_blocked | 1.000 | 1.000 | 11 |
| complaint | 1.000 | 1.000 | 8 |
| fee_question | 0.800 | 1.000 | 10 |
| fraud_report | 1.000 | 1.000 | 9 |
| general_question | 0.714 | 0.625 | 7 |
| pending_transfer | 1.000 | 1.000 | 9 |
| unclear | 0.875 | 0.778 | 8 |

## Threshold sweep (dev)

| Threshold | Coverage | Wrong draft rate |
| --- | --- | --- |
| 0.5 | 0.946 | 0.086 |
| 0.6 | 0.946 | 0.086 |
| 0.7 | 0.946 | 0.086 |
| 0.8 | 0.946 | 0.086 |
| 0.9 | 0.919 | 0.088 |

## Misses (5)

| Ticket | True class | Predicted class | Expected state | State |
| --- | --- | --- | --- | --- |
| FQ04 | fee_question | general_question | drafted | drafted |
| FQ06 | fee_question | general_question | needs_person | drafted |
| GQ07 | general_question | unclear | drafted | needs_person |
| GQ09 | general_question | unclear | drafted | needs_person |
| UN12 | unclear | general_question | needs_person | needs_person |
