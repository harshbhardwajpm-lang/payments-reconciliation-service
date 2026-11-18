"""Framework-neutral worker entry points, suitable for queue consumers or batch jobs."""

from __future__ import annotations

from typing import Any, Callable

from .pipeline import make_outgoing, reconcile_ack, verify_outgoing
from .storage import Store


def ingest(store: Store, event: dict[str, Any]) -> bool:
    if event.get("event_type") == "FRAUD":
        event["status"] = "HELD"
    return store.enqueue(event)


def prepare_release(store: Store, event: dict[str, Any], internal_new_state: dict[str, Any]) -> dict[str, Any]:
    outgoing = make_outgoing(event)
    errors = verify_outgoing(internal_new_state, outgoing)
    if errors:
        store.transition(event["event_id"], "HELD", "PRE_RELEASE_BLOCK", {"mismatches": errors})
        raise ValueError("Pre-release state mismatch: " + ",".join(errors))
    store.audit(event["event_id"], "PRE_RELEASE_MATCH", {"network": event["network"]})
    store.db.commit()
    return outgoing


def deliver(store: Store, event_id: str, outgoing: dict[str, Any], sender: Callable[[dict[str, Any]], Any], max_attempts: int = 5) -> Any:
    """Sender must use the idempotency key with its network gateway."""
    store.transition(event_id, "RELEASED", "DELIVERY_STARTED", {"network": outgoing["network"]})
    try:
        result = sender(outgoing)
        store.audit(event_id, "DELIVERED", {"network": outgoing["network"]})
        store.db.commit()
        return result
    except Exception as exc:
        store.retry_or_dead_letter(event_id, type(exc).__name__, max_attempts)
        raise


def acknowledge(store: Store, event_id: str, outgoing: dict[str, Any], ack: dict[str, Any]) -> str:
    status = reconcile_ack(outgoing, ack)
    store.transition(event_id, status, "ACKNOWLEDGED", {"ack_status": ack["status"]})
    return status
