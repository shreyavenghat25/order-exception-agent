# SOP-PAY-03: Refund amount mismatch
Applies to: REFUND_MISMATCH. Trigger: refunded amount is lower than the expected refund on a cancelled or returned order.

1. get_payment_ledger to read expected_refund and refunded.
2. initiate_refund for the gap (expected_refund minus refunded), reason "refund_gap".
3. notify_customer with template "refund_adjusted".
