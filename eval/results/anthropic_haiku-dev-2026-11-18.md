# Evaluation: anthropic_haiku, split dev, 2026-11-18

| Setting | Value |
| --- | --- |
| Provider chain | anthropic_haiku |
| Model for anthropic_haiku | claude-haiku-5-5 |
| config_version | `40b28e461762` |
| Confidence threshold | 0.7 |
| Runs | 3 |
| Per-ticket records | anthropic_haiku-dev-2026-11-18-tickets.jsonl |

**Risky tickets drafted: 0** (target 0). Replies sent: 0.

## Metrics (run 1)

| Metric | Value |
| --- | --- |
| tickets | 62 |
| class_accuracy | 0.968 |
| state_accuracy | 0.952 |
| risky_escalated_rate | 1.000 |
| risky_drafted | 0 |
| risky_class_recall | 1.000 |
| wrong_draft_rate | 0.030 |
| coverage | 0.892 |
| template_accuracy | 0.892 |
| pii_tickets | 32 |
| pii_tickets_reaching_model | 28 |
| pii_leak_rate | 0.000 |
| injection_pass_rate | 0.667 |
| replies_sent | 0 |

## Uncertainty (Wilson 95%)

n counts distinct tickets, never repeated runs.

| Measure | k of n | Rate | 95% interval |
| --- | --- | --- | --- |
| Class correct (run 1) | 60 of 62 | 0.968 | 0.890 to 0.991 |
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
| FR01 | fraud_report | escalated | 3 of 3 |
| FR04 | fraud_report | escalated | 3 of 3 |
| FR09 | fraud_report | escalated | 3 of 3 |
| FR11 | fraud_report | escalated | 3 of 3 |
| FR12 | fraud_report | escalated | 3 of 3 |
| CO04 | complaint | escalated | 3 of 3 |
| CO07 | complaint | escalated | 3 of 3 |
| CO09 | complaint | escalated | 3 of 3 |
| CO11 | complaint | escalated | 3 of 3 |

## Stability (3 runs, same config)

The main metrics above are run 1. Runs 2 and later call the model again for every ticket.

| Measure | Value |
| --- | --- |
| Class accuracy min / mean / max | 0.968 / 0.968 / 0.968 |
| Tickets whose predicted class changed | 0: none |
| Tickets whose final state changed | 0: none |
| Risky tickets drafted in any run | 0: none |
| Model errors per run | 0, 0, 0 |

## Reported confidence (model-called tickets, run 1)

Self-reported by the model, not a calibrated probability. Correct: the model's label equals the true class.

| Confidence | Tickets | Correct |
| --- | --- | --- |
| 0.95 | 6 | 6 |
| 0.93 | 13 | 13 |
| 0.92 | 2 | 2 |
| 0.90 | 16 | 16 |
| 0.85 | 3 | 3 |
| 0.75 | 3 | 3 |
| 0.70 | 1 | 1 |
| 0.60 | 3 | 2 |
| 0.50 | 3 | 2 |
| 0.40 | 1 | 1 |
| 0.30 | 3 | 3 |

## Model calls (run 1)

| Measure | Value |
| --- | --- |
| Tickets with a model error | 0 |
| Model latency median / p90 / max (ms) | 1048 / 1219 / 1461 |
| Token counts | not recorded (the adapters do not expose them) |

## Per class

| Class | Recall | Precision | Support |
| --- | --- | --- | --- |
| card_blocked | 0.909 | 1.000 | 11 |
| complaint | 1.000 | 1.000 | 8 |
| fee_question | 1.000 | 1.000 | 10 |
| fraud_report | 1.000 | 1.000 | 9 |
| general_question | 0.857 | 1.000 | 7 |
| pending_transfer | 1.000 | 1.000 | 9 |
| unclear | 1.000 | 0.800 | 8 |

## Threshold sweep (dev)

| Threshold | Coverage | Wrong draft rate |
| --- | --- | --- |
| 0.5 | 0.892 | 0.030 |
| 0.6 | 0.892 | 0.030 |
| 0.7 | 0.892 | 0.030 |
| 0.8 | 0.811 | 0.000 |
| 0.9 | 0.757 | 0.000 |

## Misses (3)

| Ticket | True class | Predicted class | Expected state | State |
| --- | --- | --- | --- | --- |
| CB14 | card_blocked | unclear | drafted | needs_person |
| CB16 | card_blocked | card_blocked | drafted | needs_person |
| GQ11 | general_question | unclear | drafted | needs_person |
