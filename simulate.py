"""Offline replay of a booking call - no keys, no audio, no network.

Feeds a scripted event timeline (what the STT and agent would emit) through
the same state, inventory and latency code the live agent uses. Useful for
checking correction handling and the latency maths before wiring up LiveKit.

Run:  python simulate.py
"""
from __future__ import annotations

from booking.inventory import Hotel, spell_reference
from booking.latency import LatencyTracker
from booking.state import BookingState

# (t_seconds, event, payload). "tool" events stand in for the LLM's tool calls.
TIMELINE = [
    (0.00, "speech", None),
    (0.40, "partial", "hi I'd like"),
    (1.10, "partial", "hi I'd like a deluxe room for two"),
    (2.30, "final", "Hi, I'd like a deluxe room for two, October 16th to the 19th."),
    (2.31, "tool", dict(room_type="deluxe", guests=2, check_in="2026-10-16", check_out="2026-10-19")),
    (3.05, "agent", "Happy to help. May I have the name for the reservation?"),
    (6.00, "speech", None),
    (7.20, "final", "Jane Okafor. Actually, make check-in the 18th, not the 16th."),
    (7.21, "tool", dict(guest_name="jane okafor", check_in="2026-10-18")),
    (7.95, "agent", "Got it, Jane: deluxe for two, October 18 to 19. Shall I check availability?"),
    (10.0, "speech", None),
    (10.6, "final", "Yes please, and book it."),
    (10.61, "quote+confirm", None),
    (11.48, "agent", "Booked. One night at 199 dollars."),
]


def main() -> None:
    state, hotel, tracker = BookingState(), Hotel(), LatencyTracker()
    for t, kind, payload in TIMELINE:
        if kind == "speech":
            tracker.on_speech_start(t)
        elif kind == "partial":
            tracker.on_partial(payload)
            print(f"{t:6.2f}s  … {payload}")
        elif kind == "final":
            tracker.on_final(payload, t)
            print(f"{t:6.2f}s  caller: {payload}")
        elif kind == "tool":
            for c in state.update(**payload):
                print(f"          correction: {c.field} {c.old} -> {c.new}")
            print(f"          state: {state.summary()}")
        elif kind == "quote+confirm":
            q = hotel.quote(state)
            ref = hotel.confirm(state)
            print(f"          quote: {q}")
            print(f"          reference: {spell_reference(ref)}")
        elif kind == "agent":
            ev = tracker.on_agent_speaking(t)
            print(f"{t:6.2f}s  agent:  {payload}")
            if ev:
                print(f"          latency: speech->final {ev['speech_to_final_ms']} ms, "
                      f"final->voice {ev['final_to_voice_ms']} ms")
    print("\nsummary:", tracker.summary())


if __name__ == "__main__":
    main()
