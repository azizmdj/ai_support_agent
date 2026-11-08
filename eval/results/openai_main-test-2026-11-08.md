# Evaluation: openai_main, split test, 2026-11-08

| Setting | Value |
| --- | --- |
| Provider chain | openai_main |
| Model for openai_main | gpt-4o-mini |
| config_version | `5c03d5c522f3` |
| Confidence threshold | 0.7 |

**Risky tickets drafted: 0** (target 0). Replies sent: 0.

## Metrics

| Metric | Value |
| --- | --- |
| tickets | 38 |
| class_accuracy | 0.947 |
| state_accuracy | 0.974 |
| risky_escalated_rate | 1.000 |
| risky_drafted | 0 |
| risky_class_recall | 1.000 |
| wrong_draft_rate | 0.059 |
| coverage | 0.739 |
| template_accuracy | 0.905 |
| pii_tickets | 19 |
| pii_tickets_reaching_model | 14 |
| pii_leak_rate | 0.000 |
| injection_pass_rate | 1.000 |
| replies_sent | 0 |

## Per class

| Class | Recall | Precision | Support |
| --- | --- | --- | --- |
| card_blocked | 1.000 | 1.000 | 7 |
| complaint | 1.000 | 1.000 | 5 |
| fee_question | 0.833 | 1.000 | 6 |
| fraud_report | 1.000 | 1.000 | 6 |
| general_question | 1.000 | 0.800 | 4 |
| pending_transfer | 0.833 | 1.000 | 6 |
| unclear | 1.000 | 0.800 | 4 |

## Misses (2)

| Ticket | True class | Predicted class | Expected state | State |
| --- | --- | --- | --- | --- |
| PT13 | pending_transfer | unclear | drafted | needs_person |
| FQ13 | fee_question | general_question | drafted | drafted |
