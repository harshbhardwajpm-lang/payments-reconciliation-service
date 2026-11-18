# Canonical reconciliation schema

The JSON Schema at `payments_reconciliation/schema.json` defines the service record. Account identifiers are vault tokens. Old/new state includes only token and expiry month/year; raw PAN, CVV, PIN, track data, and authentication data are forbidden. Amounts use integer minor units and ISO currency codes.

Each event is deterministic under its `rule_version`. `event_type` is one of expiry change, account number change, transfer, issue, or fraud. Fraud is held for manual review. Network adapters must validate actual Mastercard/Visa contracts before production use.

The outgoing record is derived from the canonical event. `verify_outgoing` compares its new state against the internal new state; any mismatch blocks release. Acknowledgements must match both event ID and idempotency key.
