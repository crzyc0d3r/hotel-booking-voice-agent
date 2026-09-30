"""Wire format for events the agent publishes to the browser.

Everything travels as UTF-8 JSON on one LiveKit data topic so the UI can
render transcripts, booking state and latency beside each other.
"""
from __future__ import annotations

import json
from typing import Any

TOPIC = "voice-telemetry"
EVENT_TYPES = {"partial", "final", "agent_state", "turn_latency", "booking", "summary"}


def encode(event_type: str, **payload: Any) -> bytes:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    return json.dumps({"type": event_type, **payload}, default=str).encode("utf-8")


def decode(data: bytes) -> dict[str, Any]:
    event = json.loads(data.decode("utf-8"))
    if event.get("type") not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event.get('type')!r}")
    return event
