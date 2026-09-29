# SOP-PAY-02: Duplicate charge
Applies to: DUPLICATE_CHARGE. Trigger: ledger shows two or more captures for one order.

1. Confirm the capture count with get_payment_ledger.
2. initiate_refund for the extra captures only (captured minus order amount minus already refunded), reason "duplicate_capture".
3. notify_customer with template "duplicate_refund".
4. Do not cancel the order. The customer still expects delivery.
