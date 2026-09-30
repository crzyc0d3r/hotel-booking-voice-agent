"""Turn-level latency bookkeeping for a voice pipeline.

Three timestamps per caller turn:

    speech_start  - the caller starts talking (VAD / user_state -> speaking)
    final         - the STT commits the final transcript for that turn
    voice_start   - the agent starts playing audio (agent_state -> speaking)

Two derived numbers are reported:

    speech_to_final  includes the caller's own speaking time, so it is NOT a
                     speech-to-text benchmark.
    final_to_voice   covers LLM generation plus time-to-first-audio of TTS -
                     the silence the caller actually notices after finishing.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Turn:
    index: int
    speech_start: float | None = None
    final: float | None = None
    voice_start: float | None = None
    transcript: str = ""
    partials: int = 0

    @property
    def speech_to_final(self) -> float | None:
        if self.speech_start is None or self.final is None:
            return None
        return self.final - self.speech_start

    @property
    def final_to_voice(self) -> float | None:
        if self.final is None or self.voice_start is None:
            return None
        return self.voice_start - self.final

    def as_event(self) -> dict:
        return {
            "type": "turn_latency",
            "turn": self.index,
            "transcript": self.transcript,
            "partials": self.partials,
            "speech_to_final_ms": _ms(self.speech_to_final),
            "final_to_voice_ms": _ms(self.final_to_voice),
        }


def _ms(v: float | None) -> int | None:
    return None if v is None else round(v * 1000)


@dataclass
class LatencyTracker:
    turns: list[Turn] = field(default_factory=list)
    _open: Turn | None = None

    def _current(self) -> Turn:
        if self._open is None:
            self._open = Turn(index=len(self.turns) + 1)
        return self._open

    def on_speech_start(self, t: float) -> None:
        turn = self._current()
        # Keep the first onset if the caller pauses and resumes mid-turn.
        if turn.speech_start is None:
            turn.speech_start = t

    def on_partial(self, text: str) -> None:
        self._current().partials += 1

    def on_final(self, text: str, t: float) -> None:
        turn = self._current()
        turn.transcript = (turn.transcript + " " + text).strip()
        turn.final = t  # last final wins: several finals can make up one turn

    def on_agent_speaking(self, t: float) -> dict | None:
        """Close the open turn when the agent starts talking; return its event."""
        turn = self._open
        if turn is None or turn.final is None:
            return None  # greeting, or agent spoke without a caller turn
        turn.voice_start = t
        self.turns.append(turn)
        self._open = None
        return turn.as_event()

    def summary(self) -> dict:
        f2v = [t.final_to_voice for t in self.turns if t.final_to_voice is not None]
        s2f = [t.speech_to_final for t in self.turns if t.speech_to_final is not None]
        def stats(xs):
            if not xs:
                return None
            xs = sorted(xs)
            p90 = xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]
            return {"median_ms": _ms(statistics.median(xs)), "p90_ms": _ms(p90), "n": len(xs)}
        return {"final_to_voice": stats(f2v), "speech_to_final": stats(s2f)}
