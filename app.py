"""Streamlit front end: join the LiveKit room, talk to the agent, watch the speech layer.

Left: live transcript (grey = partial, black = final) and agent replies.
Right: booking state as the agent captures it, plus per-turn latency.

Run:  streamlit run app.py   (with `python agent.py dev` running in another terminal)
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from livekit import api

from booking import telemetry

load_dotenv()
st.set_page_config(page_title="Hotel booking voice agent", layout="wide")


def make_token(room: str, identity: str) -> str:
    key, secret = os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")
    if not key or not secret:
        raise RuntimeError("LIVEKIT_API_KEY / LIVEKIT_API_SECRET are not set (see .env.example)")
    return (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name("Caller")
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )


st.title("Real-time hotel booking voice agent")
st.caption(
    "Speech → final includes your own speaking time; final → voice is the gap you hear "
    "after you stop talking (LLM + first TTS audio)."
)

url = os.getenv("LIVEKIT_URL", "")
if "room" not in st.session_state:
    st.session_state.room = f"hotel-{uuid.uuid4().hex[:8]}"

col1, col2 = st.columns([1, 3])
with col1:
    st.text_input("Room", key="room")
    start = st.toggle("Start call", value=False)
    st.markdown(
        "Try: *\"Hi, I'd like a deluxe room for two, October 16th to the 19th. "
        "Actually, make check-in the 18th.\"*"
    )

if not url:
    st.error("LIVEKIT_URL is not set. Copy .env.example to .env and fill in your LiveKit project.")
elif start:
    try:
        token = make_token(st.session_state.room, f"caller-{uuid.uuid4().hex[:6]}")
    except RuntimeError as exc:
        st.error(str(exc))
    else:
        html = (Path(__file__).parent / "web" / "call.html").read_text(encoding="utf-8")
        cfg = json.dumps({"url": url, "token": token, "topic": telemetry.TOPIC})
        with col2:
            components.html(html.replace("__CONFIG__", cfg), height=640, scrolling=True)
else:
    with col2:
        st.info("Toggle **Start call** and allow microphone access. The agent joins automatically.")
