# Deterministic Payments Reconciliation Agent

A small Python reference project for token-only event reconciliation. It uses **no LLM and no model tokens** for transaction decisions. Rules compare old/new state, hold fraud events, block mismatches, create a network release candidate, and reconcile acknowledgements by event ID, idempotency key, and outgoing record hash.

## Run the dry-run agent

From the repository root, initialize a local store and ingest the sample event:

```powershell
python -c "import json; from payments_reconciliation.storage import Store; from payments_reconciliation.workers import ingest; s=Store('recon.sqlite3'); ingest(s,json.load(open('tests/fixtures/event.json',encoding='utf-8'))); s.close()"
python -m payments_reconciliation.agent --db recon.sqlite3 --event-id d2719c4a-7cf6-4f39-930c-c06ad208dfe8 --seed-state tests/fixtures/internal_state.json --output outgoing.json
```

The state seed is saved in SQLite under its account token. The agent then looks up the event and current internal state from SQLite, compares them, and writes a release candidate. This command does **not** send to a card network. In a real deployment, trusted internal state is updated by a controlled internal-system adapter, not a manual JSON seed.

## Layout

- `schema.json`: canonical token-only event.
- `outgoing.schema.json`, `acknowledgement.schema.json`: outbound and response contracts.
- `pipeline.py`: deterministic comparison, classification, release generation, and acknowledgement matching.
- `agent.py`: small orchestration entry point; uses zero model tokens.
- `workers.py`: ingest, pre-release, delivery-adapter, and acknowledgement worker functions.
- `storage.py`: local SQLite reference store for token-keyed internal state and events, plus idempotency, retries, dead letters, audit, replay, and SLA counts.
- `../tests/fixtures/`: synthetic, non-production sample records.
- `../docs/`: architecture, schema, and operations notes.

## Before production

This is a portfolio/reference implementation, not a certified payment processor. Replace SQLite for the stated volume, add bank-specific input adapters, implement certified Mastercard/Visa formats and secure delivery, enforce production identity and access controls, and load-test the full deployment. Never commit credentials, real account data, or network keys. `Sender` is an adapter interface; live sending is intentionally not configured here.
