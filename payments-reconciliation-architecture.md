# Payments reconciliation service

```text
Bank adapter -> token-only canonical event -> deterministic compare/rules
             -> pre-release internal-state comparison -> network adapter
             -> exact acknowledgement reconciliation
```

The service is intentionally independent of the existing briefing application. Pure rules live in `payments_reconciliation/pipeline.py`; queue and audit primitives are in `storage.py`; orchestration entry points are in `workers.py`. No LLM participates in per-event decisions.

The included SQLite store demonstrates idempotent enqueueing, retries, dead letters, audit records, replay, and SLA counters. For 2M+ records/day and 40+ banks, deploy workers across bank/date partitions with a production transactional database and durable queue. Network file formats, bank mappings, reconciliation keys, signing, retention, throughput targets, and Mastercard/Visa certification must be supplied and validated for each partner before live delivery.

Run the example suite with `python -m unittest tests.test_payments_reconciliation`.
