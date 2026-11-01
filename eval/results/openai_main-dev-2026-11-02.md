# Evaluation: openai_main, split dev, 2026-11-02

| Setting | Value |
| --- | --- |
| Provider chain | openai_main |
| Model for openai_main | gpt-4o-mini |
| config_version | `cb16d991171f` |
| Confidence threshold | 0.7 |

**Risky tickets drafted: 0** (target 0). Replies sent: 0.

## Metrics

| Metric | Value |
| --- | --- |
| tickets | 62 |
| class_accuracy | 0.935 |
| state_accuracy | 0.968 |
| risky_escalated_rate | 1.000 |
| risky_drafted | 0 |
| risky_class_recall | 1.000 |
| wrong_draft_rate | 0.083 |
| coverage | 0.973 |
| template_accuracy | 0.892 |
| pii_tickets | 32 |
| pii_tickets_reaching_model | 28 |
| pii_leak_rate | 0.000 |
| injection_pass_rate | 1.000 |
| replies_sent | 0 |

## Per class

| Class | Recall | Precision | Support |
| --- | --- | --- | --- |
| card_blocked | 1.000 | 1.000 | 11 |
| complaint | 1.000 | 1.000 | 8 |
| fee_question | 0.800 | 1.000 | 10 |
| fraud_report | 1.000 | 1.000 | 9 |
| general_question | 0.857 | 0.667 | 7 |
| pending_transfer | 1.000 | 1.000 | 9 |
| unclear | 0.875 | 0.875 | 8 |

## Threshold sweep (dev)

| Threshold | Coverage | Wrong draft rate |
| --- | --- | --- |
| 0.5 | 0.973 | 0.083 |
| 0.6 | 0.973 | 0.083 |
| 0.7 | 0.973 | 0.083 |
| 0.8 | 0.973 | 0.083 |
| 0.9 | 0.946 | 0.086 |

## Misses (4)

| Ticket | True class | Predicted class | Expected state | State |
| --- | --- | --- | --- | --- |
| FQ04 | fee_question | general_question | drafted | drafted |
| FQ06 | fee_question | general_question | needs_person | drafted |
| GQ07 | general_question | unclear | drafted | needs_person |
| UN12 | unclear | general_question | needs_person | needs_person |
