# Payments Data Reconciliation Agent — Implementation Plan

## 1. Objective

Build a controlled reconciliation agent for a data-exchange team processing 2M+ transactions per day from 40+ banks. The agent compares the bank's raw inbound file with the normalized outbound file sent to Mastercard or Visa, identifies material changes, and produces an auditable notification work queue.

The agent must detect and classify:

- account-number changes
- expiry-date changes
- transfer cases
- issue cases
- suspected fraud cases
- missing, duplicate, late, malformed, or incorrectly routed transactions

The agent should recommend and explain actions. A separate, deterministic rules service should decide whether a card-network notification is actually sent. No LLM should be allowed to invent or silently alter transaction values.

### Concrete expiry-date example

For an existing card account, the internal system may contain an old expiry date and later receive a new expiry date. The intended flow is:

```text
Internal account history
  old expiry: 08/2026
  new expiry: 08/2028
          |
          v
Change detected: same account token + expiry changed
          |
          v
Outgoing Mastercard/Visa account-update record
          |
          v
Network acknowledgement / accepted record
          |
          v
Four-way reconciliation and release confirmation
```

The agent must prove all of the following before marking the event complete:

1. The account identity is the same according to the approved token/key.
2. The old expiry date was read from the correct internal record or account-history version.
3. The new expiry date is the current valid value and passes date-format and business-validity checks.
4. The old and new values are different; unchanged values must not create a notification.
5. The outgoing file contains the correct masked account identity, event type, old/new values where allowed, and effective date.
6. Mastercard or Visa accepted the exact outgoing record, or returned a documented rejection requiring remediation.
7. Internal state, outgoing payload, and scheme response are linked by immutable IDs and reconciled within the target SLA.

The scheme, rather than this internal agent, is responsible for distributing the account update to participating merchants such as Netflix. The agent should therefore describe the downstream merchant outcome as **scheme notification accepted**, not claim that a specific merchant has already updated its record unless a confirmed merchant response exists.

## 2. Scope and operating principles

### In scope

1. Ingest raw bank files and outgoing network files.
2. Validate file structure, control totals, schema, encoding, and record counts.
3. Normalize fields into a canonical transaction model.
4. Match raw and outgoing records using deterministic keys and controlled fallback rules.
5. Compare sensitive and operational fields.
6. Classify differences into business events.
7. Reconcile counts and monetary totals at file, bank, currency, date, and batch levels.
8. Create an exception queue, evidence package, and network-notification payload.
9. Provide replay, approval, audit, and operational metrics.

### Out of scope for the first release

- autonomous changes to source data
- autonomous final fraud decisions
- replacing Mastercard/Visa certification or scheme-specific validation
- exposing full PAN or other sensitive values in logs, prompts, dashboards, or alerts

## 3. Target architecture

```text
Bank SFTP/API files
        |
        v
Immutable landing zone + file manifest
        |
        v
Parser / schema validator ----> quarantine for invalid files
        |
        v
Canonical transaction store
        |                         \
        |                          \-- file controls and batch totals
        v
Deterministic matcher + comparator
        |
        +--> reconciled records
        +--> exceptions / event classifications
        +--> aggregate reconciliation results
        |
        v
Rules engine and network adapter
        |
        +--> Mastercard payload queue
        +--> Visa payload queue
        +--> approval / retry / delivery tracking
        |
        v
Agent: explanation, prioritization, investigation summary, runbook guidance
```

Recommended separation:

- **System of record:** relational database or lakehouse tables containing immutable file and record evidence.
- **Processing:** partitioned batch or streaming jobs, keyed by bank, business date, and file batch.
- **Rules:** versioned configuration and code, with effective dates and test fixtures per network.
- **Agent:** read-only access to masked reconciliation facts and approved runbooks; it explains and prioritizes but does not bypass controls.

## 4. Canonical transaction model

Create a canonical record before comparison. Keep both the original value and normalized value where permitted; retain raw payloads in restricted storage only.

Minimum fields:

| Group | Fields |
|---|---|
| Identity | `source_bank_id`, `file_id`, `record_number`, `transaction_id`, `network_reference`, `batch_id` |
| Account | tokenized PAN/account token, PAN last four, account-change indicator, old/new expiry month-year where legally permitted |
| Transaction | transaction type, transfer indicator, issue indicator, fraud indicator, amount, currency, transaction timestamp, settlement date |
| Routing | destination network, acquirer/issuer identifiers, merchant identifier, country, channel |
| Processing | source version, normalized version, match status, comparison status, rule version, processed timestamp |
| Controls | source hash, record hash, field-level change mask, reason codes |

Never use full PAN, CVV, PIN, track data, or raw authentication values as an agent prompt field. Use a vault-backed token and masked display values.

## 5. Record matching strategy

Matching must be deterministic and measurable. Do not use fuzzy matching for a financial posting decision.

### Primary match key

Use the strongest stable key available, ideally:

```text
source_bank_id + transaction_id + business_date + batch_id
```

If the source transaction ID is not stable, use a bank/network-approved composite key such as:

```text
source_bank_id + authorization_reference + transaction_timestamp_bucket
             + amount_minor_units + currency + merchant_token
```

### Match outcomes

- `EXACT_ONE_TO_ONE`
- `RAW_MISSING_OUTGOING`
- `OUTGOING_MISSING_RAW`
- `DUPLICATE_RAW`
- `DUPLICATE_OUTGOING`
- `AMBIGUOUS_MATCH`
- `KEY_FORMAT_ERROR`

Fallback matching is permitted only when the bank-specific adapter explicitly enables it, records the method used, and sends ambiguous results to manual review.

## 6. Comparison and event classification

Normalize before compare:

- trim and Unicode-normalize text
- standardize date/time zones and precision
- compare amounts in integer minor units
- standardize currency codes to ISO 4217
- normalize booleans and enumerations
- canonicalize account tokens without storing clear PAN

Compare field by field and write a field-level diff. Then classify with precedence so one record cannot be counted inconsistently.

Suggested precedence:

1. `DATA_INTEGRITY_FAILURE`
2. `FRAUD_CASE`
3. `ISSUE_CASE`
4. `TRANSFER_CASE`
5. `ACCOUNT_NUMBER_CHANGE`
6. `EXPIRY_DATE_CHANGE`
7. `MULTIPLE_MATERIAL_CHANGES`
8. `NON_MATERIAL_CHANGE`
9. `NO_CHANGE`

The exact precedence and notification behavior must be approved by the payment-network and compliance owners. A record may also have secondary flags, for example `TRANSFER_CASE` plus `ACCOUNT_NUMBER_CHANGE`.

### Example classification rules

| Condition | Event | Initial action |
|---|---|---|
| Tokenized account identity differs | `ACCOUNT_NUMBER_CHANGE` | Validate allowed lifecycle event; queue network notification if required |
| Expiry month/year differs | `EXPIRY_DATE_CHANGE` | Validate source authorization and scheme rules; queue notification if required |
| Transfer indicator changes to true or destination account changes | `TRANSFER_CASE` | Route to transfer workflow; require supporting reference |
| Issue indicator or issue code is present | `ISSUE_CASE` | Route to operations queue; preserve issue code and evidence |
| Fraud indicator, confirmed fraud code, or approved fraud feed match | `FRAUD_CASE` | High-priority restricted queue; require fraud policy review |
| Raw record has no outgoing counterpart | `RAW_MISSING_OUTGOING` | Investigate failed transformation or routing |
| Outgoing record has no raw counterpart | `OUTGOING_MISSING_RAW` | Block delivery/reconcile source lineage |
| Monetary or currency mismatch | `AMOUNT_OR_CURRENCY_MISMATCH` | Block or hold based on bank/network policy |

## 7. Reconciliation controls

For every run, calculate and persist:

- raw file count vs outgoing file count
- matched, unmatched, duplicate, and ambiguous counts
- raw and outgoing amount totals by currency
- transfer, issue, fraud, account-change, and expiry-change counts
- counts by bank, file, batch, transaction date, and network
- processing latency and late-file status
- hash of each input file and output artifact
- internal change event count vs generated outgoing event count
- generated outgoing event count vs scheme accepted, rejected, and pending counts
- expiry-date validity and effective-date checks for every expiry event
- age of the oldest unacknowledged event and percentage delivered within SLA

Use a tolerance of zero for counts and currency totals unless a documented scheme rule says otherwise. Do not use a monetary tolerance to hide a currency or identity mismatch.

### Four-way event reconciliation

For every material account update, persist a traceable comparison across four states:

| State | What is checked |
|---|---|
| Internal old state | Previous value and source record/version, for example expiry `08/2026` |
| Internal new state | Current value and effective timestamp, for example expiry `08/2028` |
| Outgoing state | Exact value and event written to the Mastercard/Visa file |
| Scheme response | Accepted, rejected, pending, or partially accepted result |

An expiry update is `COMPLETED` only when the outgoing state matches the internal new state and the scheme response accepts the same event. If the scheme response is pending, the status remains `PENDING_ACK`; if rejected, it becomes `FAILED_REMEDIATION` and must not be reported as delivered.

For the stated 100% accuracy objective, define the operational control as **zero unexplained differences**: every detected difference must be matched to an approved event, a documented rejection, or an investigation queue. The system can target 100% record coverage and zero unexplained variance; it cannot guarantee a merchant's behavior after the scheme accepts a file.

Run status should be one of:

- `PASSED`
- `PASSED_WITH_REVIEW`
- `FAILED_CONTROL`
- `QUARANTINED`
- `PARTIALLY_PROCESSED`

## 8. Agent behavior

The agent receives a `run_id` or an exception ID and can answer:

- What changed between raw and outgoing data?
- How many records and amounts are affected?
- Which banks, batches, currencies, or merchants are concentrated in the issue?
- Which records are missing, duplicated, or ambiguous?
- What evidence supports the recommended next action?
- Has the same rule or bank adapter failed before?

The agent must:

- cite file IDs, record numbers, rule versions, and control results
- show masked identifiers only
- distinguish facts, inferred causes, and recommendations
- refuse to release or modify a network payload
- refuse to declare a fraud case confirmed without an approved fraud decision
- use the latest approved bank/network runbook, not general model knowledge

### Example agent output

```text
Run R-2026-11-18-0042: FAILED_CONTROL
Bank: BANK_017 | Network: VISA | Batch: B-8821

Facts:
- Raw records: 2,014,883
- Outgoing records: 2,014,871
- 12 raw records have no outgoing match
- Currency totals reconcile for USD and EUR
- 3 records have expiry-date changes; 1 also has an account-token change
- 2 records are marked FRAUD_CASE and are held from release

Likely cause:
- 12 records were rejected by the outgoing adapter after schema validation.
  Evidence: adapter rejection codes E104/E117.

Recommended next action:
- Investigate the rejected records and rerun only the idempotent failed partition.
- Obtain operations approval before sending the two fraud-related notifications.
```

## 9. Processing workflow

1. Register inbound file and compute SHA-256 hash.
2. Verify sender, signature, encryption, naming convention, and expected delivery window.
3. Read the old and current account states from the internal database using a consistent snapshot.
4. Parse the bank/source update using the bank-specific adapter version.
5. Validate schema, mandatory fields, enumerations, dates, account identity, and control totals.
6. Create an internal change event only when the approved comparison detects a material difference.
7. Write immutable raw-file, internal-state, and canonical-record evidence.
8. Create network payloads only for approved, eligible events.
9. Register and hash the outgoing Mastercard/Visa file.
10. Compare internal new state to the outgoing state before release; stop the file on any material mismatch.
11. Send through an idempotent delivery queue with retry and dead-letter handling.
12. Ingest scheme acknowledgements and compare the accepted/rejected record to the exact outgoing event.
13. Update the event status and SLA timer; keep pending or rejected events open.
14. Publish an operational summary and retain all evidence according to policy.

## 10. Idempotency, replay, and failure handling

- Idempotency key: `network + source_bank_id + transaction_id + event_type + effective_date + rule_version`.
- Never resend a notification solely because a processing job was retried.
- Store immutable run snapshots so a historical comparison can be replayed with the same adapter and rule versions.
- Quarantine malformed files; do not partially accept a file unless the bank contract explicitly supports record-level acceptance.
- Use dead-letter queues for records that fail serialization, validation, or network delivery.
- Require explicit approval for replaying a notification after the network has acknowledged it.

## 11. Security and governance

- Encrypt files and databases at rest and in transit.
- Tokenize or vault-protect PAN/account numbers; mask all UI and agent output.
- Apply least-privilege roles: ingestion, reconciliation, investigation, approval, delivery, audit.
- Keep an append-only audit trail for file access, rule decisions, approvals, payload creation, and delivery responses.
- Define retention and deletion rules with legal, PCI DSS, privacy, and scheme obligations.
- Keep Mastercard and Visa adapters and rule packs separate; do not assume one network's event semantics apply to the other.
- Obtain network specifications and certification requirements from the relevant Mastercard/Visa program owners before production release.

## 12. Delivery phases

### Phase 0 — Discovery and controls

- collect sample raw and outgoing files from each bank
- document data contracts, fields, keys, control totals, transfer/issue/fraud codes
- confirm Mastercard/Visa notification obligations and approval roles
- define PCI/privacy boundaries and retention

### Phase 1 — Reconciliation MVP

- onboard 2–3 representative banks
- implement immutable manifests, adapters, canonical model, one-to-one matching, field diffs, and control dashboard
- produce exception reports without sending network notifications
- validate results against known historical files

### Phase 2 — Notification workflow

- add versioned network rules and payload schemas
- add approval queue, idempotent delivery, acknowledgements, retries, and dead letters
- introduce masked agent explanations and runbook retrieval

### Phase 3 — Scale and operations

- partition processing by bank/date/batch
- benchmark at least 2x the peak daily volume
- add backpressure, late-file monitoring, alerting, and disaster recovery
- onboard remaining banks through adapter certification tests

## 13. Acceptance criteria

- 100% of input files have a manifest, hash, source, business date, and processing status.
- Every canonical record has a traceable raw record and, when present, outgoing record.
- Matching is deterministic, reproducible, and produces a reason code for every non-exact result.
- Count and amount controls are calculated by bank, currency, batch, and network.
- No duplicate notification is generated after a retry or replay.
- Full PAN and other prohibited sensitive values never appear in logs, prompts, dashboards, or exception exports.
- Historical golden files reproduce expected match and event results.
- Load tests demonstrate safe processing at the required peak plus operational headroom.
- Network acknowledgements are linked to the exact notification and source event.

## 14. Suggested implementation components

```text
adapters/
  bank_<id>.py                 # parse and normalize bank-specific files
schemas/
  canonical_transaction.json
  mastercard_events.json
  visa_events.json
reconciliation/
  manifest.py
  matching.py
  comparison.py
  controls.py
  classifications.py
delivery/
  idempotency.py
  mastercard_adapter.py
  visa_adapter.py
agent/
  tools.py                     # read-only queries over masked facts
  prompts.md                   # behavior and safety contract
tests/fixtures/
  golden_raw/
  golden_outgoing/
  expected_results/
```

The first technical decision should be the canonical schema and match-key contract for each bank. Once those are approved, the rest of the agent can be built and tested incrementally without coupling business-critical reconciliation to free-form model output.
