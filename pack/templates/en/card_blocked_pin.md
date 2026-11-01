---
id: card_blocked_pin
class: card_blocked
lang: en
version: 1
required_fields: [customer_name, card_last4]
synthetic: true
---
Hello {{customer_name}},

your card ending in {{card_last4}} was blocked for your safety after several incorrect PIN entries.

You can view your PIN in the app under Cards > Show PIN. After that, the block is lifted the next time you make a payment with the correct PIN at a terminal or ATM.

If the card is still blocked after that, reply to this message and we will look into it.

Kind regards,
Your support team
