"""LiveKit worker: the real-time hotel booking voice agent.

Pipeline per caller turn:
    browser mic --(LiveKit room)--> Speechmatics Linden STT (partials + finals)
    final turn --> OpenRouter LLM (tool calls update the reservation)
    reply text --> Fish Audio TTS --(LiveKit room)--> browser speaker

Speechmatics and Fish Audio run through LiveKit Inference, so only LiveKit
project credentials and an OpenRouter key are needed.

Run:  python agent.py dev
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    cli,
    function_tool,
    inference,
)
from livekit.plugins import openai, silero

from booking import telemetry
from booking.inventory import Hotel, spell_reference
from booking.latency import LatencyTracker
from booking.prompts import GREETING, instructions
from booking.state import BookingError, BookingState

load_dotenv()
log = logging.getLogger("hotel-agent")

STT_MODEL = os.getenv("STT_MODEL", "speechmatics/linden-1")
TTS_MODEL = os.getenv("TTS_MODEL", "fishaudio/s2-pro")
LLM_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")

HOTEL = Hotel()  # shared per worker process; replace with your PMS client


class Publisher:
    """Fire-and-forget JSON events on the telemetry data topic."""

    def __init__(self, ctx: JobContext):
        self._ctx = ctx
        self._tasks: set[asyncio.Task] = set()

    def send(self, event_type: str, **payload) -> None:
        data = telemetry.encode(event_type, **payload)
        task = asyncio.create_task(
            self._ctx.room.local_participant.publish_data(data, reliable=True, topic=telemetry.TOPIC)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


class BookingAgent(Agent):
    def __init__(self, publisher: Publisher) -> None:
        super().__init__(instructions=instructions())
        self.pub = publisher

    def _state(self, ctx: RunContext[BookingState]) -> BookingState:
        return ctx.userdata

    @function_tool
    async def update_reservation(
        self,
        ctx: RunContext[BookingState],
        guest_name: str | None = None,
        check_in: str | None = None,
        check_out: str | None = None,
        guests: int | None = None,
        room_type: str | None = None,
    ) -> str:
        """Record or correct reservation details. Pass only the fields the caller just gave or changed.

        Args:
            guest_name: Full name of the guest.
            check_in: Check-in date, YYYY-MM-DD.
            check_out: Check-out date, YYYY-MM-DD.
            guests: Number of guests (1-6).
            room_type: One of standard, deluxe, suite.
        """
        state = self._state(ctx)
        try:
            changes = state.update(
                guest_name=guest_name, check_in=check_in, check_out=check_out,
                guests=guests, room_type=room_type,
            )
        except BookingError as exc:
            return f"Could not update: {exc}. Ask the caller to clarify."
        self.pub.send("booking", state=state.to_dict())
        note = "".join(f" Corrected {c.field} from {c.old} to {c.new}." for c in changes)
        return state.summary() + note

    @function_tool
    async def check_availability(self, ctx: RunContext[BookingState]) -> str:
        """Check availability and price once dates and room type are known."""
        state = self._state(ctx)
        try:
            q = HOTEL.quote(state)
        except BookingError as exc:
            return f"Cannot quote yet: {exc}."
        if q["available"]:
            return (f"Available. {q['nights']} nights at {q['nightly_rate']} dollars, "
                    f"total {q['total']} dollars. Ask the caller to confirm.")
        alts = ", ".join(q["alternatives"]) or "none"
        return f"Not available for those dates. Alternatives with space: {alts}."

    @function_tool
    async def confirm_reservation(self, ctx: RunContext[BookingState]) -> str:
        """Confirm the booking. Only call after the caller explicitly agrees."""
        state = self._state(ctx)
        try:
            ref = HOTEL.confirm(state)
        except BookingError as exc:
            return f"Cannot confirm: {exc}."
        self.pub.send("booking", state=state.to_dict())
        return f"Confirmed. Reference code, read it one character at a time: {spell_reference(ref)}."


server = AgentServer(setup_fnc=lambda proc: proc.userdata.update(vad=silero.VAD.load()))


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    pub = Publisher(ctx)
    tracker = LatencyTracker()

    session: AgentSession[BookingState] = AgentSession(
        userdata=BookingState(),
        vad=ctx.proc.userdata["vad"],
        # Partials stay on so the UI can show text evolving before the final commit.
        stt=inference.STT(model=STT_MODEL, language="en", extra_kwargs={"enable_partials": True}),
        llm=openai.LLM.with_openrouter(model=LLM_MODEL, api_key=os.getenv("OPENROUTER_API_KEY")),
        tts=inference.TTS(model=TTS_MODEL, extra_kwargs={"latency": "balanced"}),
    )

    @session.on("user_state_changed")
    def _user_state(ev):
        if ev.new_state == "speaking":
            tracker.on_speech_start(ev.created_at)

    @session.on("user_input_transcribed")
    def _transcribed(ev):
        if ev.is_final:
            tracker.on_final(ev.transcript, ev.created_at)
            pub.send("final", text=ev.transcript, t=ev.created_at)
        else:
            tracker.on_partial(ev.transcript)
            pub.send("partial", text=ev.transcript, t=ev.created_at)

    @session.on("agent_state_changed")
    def _agent_state(ev):
        pub.send("agent_state", state=ev.new_state, t=ev.created_at)
        if ev.new_state == "speaking":
            event = tracker.on_agent_speaking(ev.created_at)
            if event:
                pub.send("turn_latency", **{k: v for k, v in event.items() if k != "type"})
                pub.send("summary", **tracker.summary())

    async def _log_summary():
        log.info("latency summary: %s", tracker.summary())

    ctx.add_shutdown_callback(_log_summary)

    await session.start(agent=BookingAgent(pub), room=ctx.room)
    await session.generate_reply(instructions=GREETING)


if __name__ == "__main__":
    cli.run_app(server)
