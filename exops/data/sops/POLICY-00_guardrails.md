# POLICY-00: Autonomy and approval policy
- Refunds above Rs 5000 require human approval. Refunds can never exceed the refundable balance (captured minus refunded).
- Replacements (create_replacement) always require approval.
- At most 3 write actions per exception.
- If classification or plan confidence is below 0.70, route to a human.
- Customer PII (phone numbers) must be masked before being sent to any model.
- Every write action is logged with its compensation (rollback) action where one exists.
