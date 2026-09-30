"""Reservation state that survives mid-sentence corrections.

The LLM never owns the reservation. It proposes field updates through a tool
call; this module validates them, records what changed (so a caller can say
"actually, make that the 18th" without restarting), and reports which fields
are still missing so the agent only asks for those.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any

REQUIRED_FIELDS = ("guest_name", "check_in", "check_out", "guests", "room_type")
ROOM_TYPES = ("standard", "deluxe", "suite")


class BookingError(ValueError):
    """Raised when a proposed update is invalid."""


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:  # pragma: no cover - message path tested
        raise BookingError(f"dates must be ISO formatted (YYYY-MM-DD), got {value!r}") from exc


@dataclass
class Correction:
    field: str
    old: Any
    new: Any


@dataclass
class BookingState:
    guest_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    guests: int | None = None
    room_type: str | None = None
    reference: str | None = None
    corrections: list[Correction] = field(default_factory=list)

    # ------------------------------------------------------------------ update
    def update(self, **proposed: Any) -> list[Correction]:
        """Apply non-empty proposed values; return the corrections made.

        Validation happens on a copy first so an invalid update never leaves
        the state half-applied.
        """
        if self.reference:
            raise BookingError("reservation already confirmed; start a new booking to change it")
        clean: dict[str, Any] = {}
        for key, value in proposed.items():
            if key not in REQUIRED_FIELDS:
                raise BookingError(f"unknown field {key!r}")
            if value in (None, ""):
                continue
            if key in ("check_in", "check_out"):
                value = parse_date(value)
            elif key == "guests":
                value = int(value)
                if not 1 <= value <= 6:
                    raise BookingError("guests must be between 1 and 6")
            elif key == "room_type":
                value = str(value).lower().strip()
                if value not in ROOM_TYPES:
                    raise BookingError(f"room_type must be one of {', '.join(ROOM_TYPES)}")
            elif key == "guest_name":
                value = " ".join(str(value).split()).title()
            clean[key] = value

        check_in = clean.get("check_in", self.check_in)
        check_out = clean.get("check_out", self.check_out)
        if check_in and check_out and check_out <= check_in:
            raise BookingError("check_out must be after check_in")

        made: list[Correction] = []
        for key, value in clean.items():
            old = getattr(self, key)
            if old is not None and old != value:
                made.append(Correction(key, old, value))
            setattr(self, key, value)
        self.corrections.extend(made)
        return made

    # ----------------------------------------------------------------- queries
    def missing_fields(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if getattr(self, f) is None]

    def is_complete(self) -> bool:
        return not self.missing_fields()

    def nights(self) -> int | None:
        if self.check_in and self.check_out:
            return (self.check_out - self.check_in).days
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("check_in", "check_out"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        d["corrections"] = [
            {"field": c.field, "old": str(c.old), "new": str(c.new)} for c in self.corrections
        ]
        d["missing"] = self.missing_fields()
        return d

    def summary(self) -> str:
        """Short, speakable status used as a tool result for the LLM."""
        known = []
        if self.guest_name:
            known.append(f"name {self.guest_name}")
        if self.check_in:
            known.append(f"check-in {self.check_in:%B} {self.check_in.day}")
        if self.check_out:
            known.append(f"check-out {self.check_out:%B} {self.check_out.day}")
        if self.guests:
            known.append(f"{self.guests} guest{'s' if self.guests > 1 else ''}")
        if self.room_type:
            known.append(f"{self.room_type} room")
        missing = self.missing_fields()
        tail = f" Still missing: {', '.join(missing)}." if missing else " All details captured."
        return ("Captured " + ", ".join(known) + "." if known else "Nothing captured yet.") + tail
