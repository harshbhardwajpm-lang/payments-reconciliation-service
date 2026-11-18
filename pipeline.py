"""Pure deterministic comparison, classification, and release checks."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

EVENTS = {"EXPIRY_DATE_CHANGE", "ACCOUNT_NUMBER_CHANGE", "TRANSFER", "ISSUE", "FRAUD"}
NETWORKS = {"MASTERCARD", "VISA"}


def compare_states(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Return stable field paths that differ; account identifiers are tokens."""
    changed = []
    for key in ("account_token", "expiry_month", "expiry_year"):
        if old.get(key) != new.get(key):
            changed.append(key)
    return changed


def classify(old: dict[str, Any], new: dict[str, Any], flags: dict[str, Any] | None = None) -> list[str]:
    flags = flags or {}
    changes = compare_states(old, new)
    events = []
    if "expiry_month" in changes or "expiry_year" in changes:
        events.append("EXPIRY_DATE_CHANGE")
    if "account_token" in changes:
        events.append("ACCOUNT_NUMBER_CHANGE")
    if flags.get("transfer") is True:
        events.append("TRANSFER")
    if flags.get("issue") is True:
        events.append("ISSUE")
    if flags.get("fraud") is True:
        events.append("FRAUD")
    return events


def make_outgoing(event: dict[str, Any]) -> dict[str, Any]:
    """Build a network record from canonical data; never enrich or infer values."""
    event_type = event["event_type"]
    network = event["network"]
    if event_type not in EVENTS or network not in NETWORKS:
        raise ValueError("Unsupported event type or network")
    if event_type == "FRAUD":
        raise ValueError("Fraud events require manual review and cannot be released")
    if event.get("status") == "HELD":
        raise ValueError("Held event cannot be released")
    record = {
        "event_id": event["event_id"],
        "idempotency_key": event["idempotency_key"],
        "network": network,
        "event_type": event_type,
        "account_token": event["identity"]["account_token"],
        "old_state": deepcopy(event["old_state"]),
        "new_state": deepcopy(event["new_state"]),
        "effective_date": event.get("effective_date"),
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["outgoing_sha256"] = hashlib.sha256(canonical).hexdigest()
    return record


def verify_outgoing(internal_new_state: dict[str, Any], outgoing: dict[str, Any]) -> list[str]:
    """Return mismatched paths; caller must block release if this is nonempty."""
    mismatches = []
    sent = outgoing.get("new_state", {})
    for key in ("account_token", "expiry_month", "expiry_year"):
        if internal_new_state.get(key) != sent.get(key):
            mismatches.append("new_state." + key)
    if outgoing.get("account_token") != internal_new_state.get("account_token"):
        mismatches.append("account_token")
    return mismatches


def reconcile_ack(outgoing: dict[str, Any], ack: dict[str, Any]) -> str:
    """Accept only an acknowledgement for the exact outgoing event and key."""
    if (ack.get("event_id") != outgoing.get("event_id")
            or ack.get("idempotency_key") != outgoing.get("idempotency_key")
            or ack.get("outgoing_sha256") != outgoing.get("outgoing_sha256")):
        raise ValueError("Acknowledgement does not match outgoing record")
    if ack.get("status") not in {"ACCEPTED", "REJECTED"}:
        raise ValueError("Unknown acknowledgement status")
    return "ACK_" + ack["status"]
