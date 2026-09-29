# SOP-PAY-01: Payment captured but order not confirmed
Applies to: PAYMENT_ORDER_MISMATCH. Trigger: payment captured more than 30 minutes ago while order is PAYMENT_PENDING.

1. Check the ledger with get_payment_ledger. Exactly one capture is expected. Two or more captures means follow SOP-PAY-02 instead.
2. check_inventory. If the item is available: reconcile_payment to confirm the order, then notify_customer with template "order_confirmed".
3. If inventory is unavailable: initiate_refund for the full captured amount with reason "order_not_fulfillable", then notify_customer with template "refund_initiated".
4. A gateway retry alone does not mean a duplicate charge. Always trust the ledger capture count.
