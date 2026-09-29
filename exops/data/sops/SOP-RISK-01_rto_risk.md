# SOP-RISK-01: Return-to-origin (RTO) risk on COD orders
Applies to: RTO_RISK. Trigger: COD order with low address quality or prior RTOs.

1. get_risk_profile for address_score and prior_rto.
2. If address_score is below 0.4: request_address_verification and hold_shipment until the address is confirmed.
3. If address_score is 0.4 or higher: request_address_verification and notify_customer with template "prepaid_nudge" (offer a prepaid discount). Do not hold.
4. Never cancel a COD order automatically.
