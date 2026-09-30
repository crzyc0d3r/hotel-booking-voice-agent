"""A tiny in-memory hotel: nightly rates, room counts and existing bookings.

Swap this module for your property-management-system client in production;
the agent only depends on `check_availability` and `confirm`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta

from .state import BookingError, BookingState

RATES = {"standard": 139, "deluxe": 199, "suite": 349}
CAPACITY = {"standard": 2, "deluxe": 3, "suite": 6}


@dataclass
class Hotel:
    rooms: dict[str, int] = field(default_factory=lambda: {"standard": 10, "deluxe": 5, "suite": 2})
    # (room_type, night) -> rooms already sold
    sold: dict[tuple[str, date], int] = field(default_factory=dict)

    def _nights(self, check_in: date, check_out: date):
        d = check_in
        while d < check_out:
            yield d
            d += timedelta(days=1)

    def available(self, room_type: str, check_in: date, check_out: date) -> bool:
        return all(
            self.sold.get((room_type, n), 0) < self.rooms[room_type]
            for n in self._nights(check_in, check_out)
        )

    def quote(self, state: BookingState) -> dict:
        if not (state.room_type and state.check_in and state.check_out):
            raise BookingError("need room_type, check_in and check_out to quote")
        if state.guests and state.guests > CAPACITY[state.room_type]:
            raise BookingError(
                f"a {state.room_type} room holds at most {CAPACITY[state.room_type]} guests"
            )
        ok = self.available(state.room_type, state.check_in, state.check_out)
        nights = state.nights() or 0
        alternatives = [
            rt for rt in RATES
            if rt != state.room_type and self.available(rt, state.check_in, state.check_out)
            and (not state.guests or state.guests <= CAPACITY[rt])
        ]
        return {
            "available": ok,
            "nights": nights,
            "nightly_rate": RATES[state.room_type],
            "total": RATES[state.room_type] * nights,
            "alternatives": [] if ok else alternatives,
        }

    def confirm(self, state: BookingState) -> str:
        if not state.is_complete():
            raise BookingError(f"missing fields: {', '.join(state.missing_fields())}")
        q = self.quote(state)
        if not q["available"]:
            raise BookingError(f"no {state.room_type} rooms left for those dates")
        for n in self._nights(state.check_in, state.check_out):
            key = (state.room_type, n)
            self.sold[key] = self.sold.get(key, 0) + 1
        raw = f"{state.guest_name}|{state.check_in}|{state.check_out}|{state.room_type}"
        digest = hashlib.sha1(raw.encode()).hexdigest().upper()
        # Avoid characters that are easily confused when spoken (0/O, 1/I).
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        ref = "".join(alphabet[int(digest[i:i + 2], 16) % len(alphabet)] for i in range(0, 12, 2))
        state.reference = ref
        return ref


def spell_reference(ref: str) -> str:
    """Render a booking reference the way it should be spoken: one symbol at a time."""
    return ", ".join(ref)
