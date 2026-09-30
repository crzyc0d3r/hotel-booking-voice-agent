from datetime import date

import pytest

from booking import telemetry
from booking.inventory import Hotel, spell_reference
from booking.latency import LatencyTracker
from booking.prompts import instructions
from booking.state import BookingError, BookingState


def full_state(**over):
    s = BookingState()
    s.update(**{**dict(guest_name="ada lovelace", check_in="2026-10-16", check_out="2026-10-19",
                       guests=2, room_type="Deluxe"), **over})
    return s


def test_missing_fields_shrink_as_details_arrive():
    s = BookingState()
    assert len(s.missing_fields()) == 5
    s.update(room_type="suite", guests=3)
    assert s.missing_fields() == ["guest_name", "check_in", "check_out"]


def test_correction_keeps_other_fields_and_is_recorded():
    s = full_state()
    changes = s.update(check_in="2026-10-18")
    assert s.check_in == date(2026, 10, 18)
    assert s.guest_name == "Ada Lovelace" and s.room_type == "deluxe"
    assert [(c.field, str(c.new)) for c in changes] == [("check_in", "2026-10-18")]
    assert s.nights() == 1


def test_invalid_update_is_atomic():
    s = full_state()
    with pytest.raises(BookingError):
        s.update(guests=2, check_out="2026-10-10")  # before check-in
    assert s.check_out == date(2026, 10, 19)


def test_rejects_bad_room_and_guest_count():
    s = BookingState()
    with pytest.raises(BookingError):
        s.update(room_type="penthouse")
    with pytest.raises(BookingError):
        s.update(guests=9)


def test_quote_confirm_and_sell_out():
    hotel = Hotel(rooms={"standard": 1, "deluxe": 1, "suite": 1})
    a = full_state()
    q = hotel.quote(a)
    assert q["available"] and q["total"] == 3 * 199
    ref = hotel.confirm(a)
    assert len(ref) == 6 and not set(ref) & set("01IO")
    b = full_state(guest_name="grace hopper")
    q2 = hotel.quote(b)
    assert not q2["available"] and "standard" in q2["alternatives"]
    with pytest.raises(BookingError):
        hotel.confirm(b)


def test_confirmed_booking_is_frozen():
    hotel, s = Hotel(), full_state()
    hotel.confirm(s)
    with pytest.raises(BookingError):
        s.update(guests=1)


def test_capacity_check():
    with pytest.raises(BookingError):
        Hotel().quote(full_state(room_type="standard", guests=4))


def test_spell_reference():
    assert spell_reference("AB3") == "A, B, 3"


def test_latency_tracker_turns():
    t = LatencyTracker()
    assert t.on_agent_speaking(0.5) is None  # greeting: no caller turn yet
    t.on_speech_start(1.0)
    t.on_partial("hel")
    t.on_speech_start(1.5)  # resumed after a pause: keep first onset
    t.on_final("hello", 2.0)
    ev = t.on_agent_speaking(2.8)
    assert ev["speech_to_final_ms"] == 1000 and ev["final_to_voice_ms"] == 800
    assert ev["partials"] == 1
    t.on_speech_start(5.0); t.on_final("a", 5.5); t.on_final("b", 6.0); t.on_agent_speaking(6.4)
    assert t.turns[-1].transcript == "a b"
    s = t.summary()
    assert s["final_to_voice"]["n"] == 2 and s["final_to_voice"]["median_ms"] == 600


def test_telemetry_roundtrip():
    ev = telemetry.decode(telemetry.encode("final", text="hi", t=1.0))
    assert ev == {"type": "final", "text": "hi", "t": 1.0}
    with pytest.raises(ValueError):
        telemetry.encode("bogus")


def test_prompt_mentions_today():
    assert "2026-01-02" in instructions(date(2026, 1, 2))
