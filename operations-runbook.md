# Operations runbook

- Keep inbound files in encrypted, access-controlled landing storage; pass tokenized records only to this service.
- Partition work by bank, business date, and file. Track file hash, count, and amount controls in the bank adapter before enqueueing records.
- Retry transient delivery errors with bounded exponential backoff. After five attempts, inspect the dead-letter record and correct the cause before replay.
- Check `Store.metrics(sla_seconds)` for pending, overdue, and dead-letter counts. Alert on overdue work, control-total mismatches, elevated rejection rates, and missing bank/network files.
- Replay uses the immutable event snapshot and records an audit entry. Replaying an acknowledged event requires explicit approval and never causes an automatic resend.
- Retain audit evidence per approved policy. Audit rows are append-only. Never include raw account data or credentials in event payloads, exception details, or logs.
- Use a managed transactional database, durable queue, secrets manager, encryption, access controls, network-certified adapters, and load-tested partitioning for production deployment. SQLite is the local reference backend, not a 2M/day deployment topology.
