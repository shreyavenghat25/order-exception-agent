# SOP-LOG-01: Shipment stuck in transit
Applies to: SHIPMENT_STUCK. Trigger: no courier scan for 48 hours or more.

1. Pull tracking with get_tracking and confirm the last scan age and the hub.
2. If the last scan is between 48 and 120 hours: raise_courier_ticket with the hub name, then notify_customer with template "delay_apology".
3. If the last scan is 120 hours or more, treat the shipment as presumed lost: create_replacement and notify_customer with template "replacement_created". A replacement is HIGH risk and needs approval from an operations lead.
4. Never refund AND replace the same order.
