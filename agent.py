"""Small deterministic event runner; no LLM or per-transaction model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from .storage import Store
from .workers import deliver, prepare_release


Sender = Callable[[dict[str, Any]], Any]


def lookup_event(store: Store, event_id: str) -> dict[str, Any]:
    """Load the immutable canonical event by its stable ID."""
    row = store.db.execute("SELECT payload FROM events WHERE event_id=?", (event_id,)).fetchone()
    if row is None:
        raise KeyError("Event not found: " + event_id)
    return json.loads(row["payload"])


def run_event(
    store: Store,
    event_id: str,
    internal_new_state: dict[str, Any] | None = None,
    sender: Sender | None = None,
) -> dict[str, Any]:
    """Look up, compare, then return a release candidate or deliver through an adapter."""
    event = lookup_event(store, event_id)
    if internal_new_state is None:
        saved = store.get_account_state(event["identity"]["account_token"])
        if saved is None:
            raise KeyError("No internal state for account token: " + event["identity"]["account_token"])
        internal_new_state = {key: saved[key] for key in ("account_token", "expiry_month", "expiry_year")}
    outgoing = prepare_release(store, event, internal_new_state)
    if sender is not None:
        deliver(store, event_id, outgoing, sender)
    return outgoing


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic payment reconciliation (dry-run)")
    parser.add_argument("--db", required=True, help="SQLite reference-store path")
    parser.add_argument("--event-id", required=True, help="Canonical event ID already in the store")
    parser.add_argument("--seed-state", help="Load synthetic current state JSON into the database before lookup")
    parser.add_argument("--output", help="Write outgoing release candidate to this JSON file")
    args = parser.parse_args()

    store = Store(args.db)
    try:
        if args.seed_state:
            seed = json.loads(Path(args.seed_state).read_text(encoding="utf-8"))
            store.save_account_state(seed, "demo-seed")
        outgoing = run_event(store, args.event_id)
        rendered = json.dumps(outgoing, indent=2, sort_keys=True)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
