# SOP-LOG-02: Delivery promise (SLA) at risk
Applies to: SLA_BREACH_RISK. Trigger: courier ETA is later than the promised delivery time, with a recent scan.

1. get_tracking for eta_hours and courier_on_time_rate.
2. If courier_on_time_rate is below 0.80: reassign_courier to the best-performing courier (BlueDart by default, or Ekart if it is already BlueDart).
3. Otherwise: expedite_shipment with the current courier.
4. If the ETA is more than 24 hours past the promise, also notify_customer with template "delay_heads_up".
5. If the last scan is older than 48 hours, it is a stuck shipment. Use SOP-LOG-01.
