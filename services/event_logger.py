#!/usr/bin/env python3
"""
Production-Grade SSE → WebSocket Event Bridge + PostgreSQL Logger
Fixes the parsing issues from File 1 by adopting the SDK's robust logic.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

import aiohttp
import websockets
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from websockets.asyncio.server import ServerConnection

from hp2.core.settings import get_settings, get_sql_logging_settings

# --- Path / Settings Setup ---
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


_settings = get_settings()
_sql_settings = get_sql_logging_settings()

# --- Config ---
TEAM_ID = str(_settings.hackapizza_team_id)
API_KEY = _settings.hackapizza_team_api_key
DB_URL = _sql_settings.hackapizza_sql_connstr
BASE_URL = "https://hackapizza.datapizza.tech"
WS_HOST = os.environ.get("WS_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("WS_PORT", "8765"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("Bridge")


# --- Database Schema ---
class Base(DeclarativeBase):
    pass


class EventLog(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_timestamp", "timestamp_utc"),
        Index("idx_events_event_type", "event_type"),
        Index("idx_events_turn_id", "turn_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    turn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class GameStartedEvent(Base):
    __tablename__ = "event_game_started"
    __table_args__ = (Index("idx_game_started_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class GamePhaseChangedEvent(Base):
    __tablename__ = "event_game_phase_changed"
    __table_args__ = (Index("idx_phase_changed_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String, nullable=False)


class ClientOrderEvent(Base):
    __tablename__ = "event_client_spawned"
    __table_args__ = (Index("idx_client_spawned_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    client_name: Mapped[str] = mapped_column(String, nullable=False)
    order_text: Mapped[str] = mapped_column(String, nullable=False)


class PreparationCompleteEvent(Base):
    __tablename__ = "event_preparation_complete"
    __table_args__ = (Index("idx_prep_complete_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    dish_name: Mapped[str] = mapped_column(String, nullable=False)


class NewMessageEvent(Base):
    __tablename__ = "event_new_message"
    __table_args__ = (
        Index("idx_new_message_event_id", "event_id"),
        Index("idx_new_message_sender_id", "sender_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    sender_id: Mapped[str] = mapped_column(String, nullable=False)
    sender_name: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    message_datetime: Mapped[Optional[str]] = mapped_column(String, nullable=True)

engine = create_engine(DB_URL, future=True)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- State ---
_ws_clients: Set[ServerConnection] = set()
_current_turn_id: Optional[str] = None

# --- Core Logic ---


def persist_to_db(event_type: str, data: Dict[str, Any]):
    """Persists the event to PostgreSQL (generic + typed tables)."""
    global _current_turn_id

    # Skip heartbeats entirely
    if event_type == "heartbeat":
        return

    # Update Turn Tracking
    if event_type == "game_started":
        _current_turn_id = str(
            data.get("turn_id")
        )

    # Only store raw JSON for unknown event types
    is_known = event_type in (
        "game_started",
        "game_phase_changed",
        "client_spawned",
        "preparation_complete",
        "new_message",
        "message",
    )

    try:
        with Session() as session:
            # 1) Generic events row
            event_row = EventLog(
                timestamp_utc=datetime.now(UTC),
                turn_id=_current_turn_id,
                event_type=event_type,
                data_json=None if is_known else json.dumps(data, default=str),
            )
            session.add(event_row)
            session.flush()
            event_id = event_row.id

            # 2) Typed table for known event types
            if event_type == "game_started":
                session.add(
                    GameStartedEvent(
                        event_id=event_id,
                        turn_id=_current_turn_id,
                    )
                )
            elif event_type == "game_phase_changed":
                session.add(
                    GamePhaseChangedEvent(
                        event_id=event_id,
                        phase=data.get("phase", "unknown"),
                    )
                )
            elif event_type == "client_spawned":
                session.add(
                    ClientOrderEvent(
                        event_id=event_id,
                        client_name=data.get("clientName", "unknown"),
                        order_text=data.get("orderText", "unknown"),
                    )
                )
            elif event_type == "preparation_complete":
                session.add(
                    PreparationCompleteEvent(
                        event_id=event_id,
                        dish_name=data.get("dish", "unknown"),
                    )
                )
            elif event_type == "new_message":
                session.add(
                    NewMessageEvent(
                        event_id=event_id,
                        message_id=data.get("messageId", ""),
                        sender_id=data.get("senderId", ""),
                        sender_name=data.get("senderName", ""),
                        text=data.get("text", ""),
                        message_datetime=data.get("datetime"),
                    )
                )
            elif event_type == "message":
                session.add(
                    NewMessageEvent(
                        event_id=event_id,
                        message_id="",
                        sender_id="-1",
                        sender_name=data.get("sender", "unknown"),
                        text=data.get("payload", ""),
                        message_datetime=None,
                    )
                )

            session.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")


async def broadcast(message: str):
    """Sends event to all connected WebSockets."""
    if not _ws_clients:
        return
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        _ws_clients.discard(ws)


async def handle_sse_payload(json_str: str):
    """Parses, logs, and broadcasts a single JSON payload."""
    try:
        event_json = json.loads(json_str)
        event_type = event_json.get("type", "unknown")
        data = event_json.get("data", {})

        # Normalize non-dict data (just like File 2)
        if not isinstance(data, dict):
            data = {"value": data}

        # 1. Log it
        persist_to_db(event_type, data)

        # 2. Broadcast it
        # Inject turn_id
        if _current_turn_id:
            event_json["turn_id"] = _current_turn_id
        await broadcast(json.dumps(event_json))

        logger.info(f"Relayed Event: {str(event_json)[:250]}...")
    except json.JSONDecodeError:
        logger.warning(f"Malformed JSON skipped: {json_str[:50]}...")


async def listen_to_sse(stop_event: asyncio.Event):
    """The main listener loop with 'File 2' style robust parsing."""
    headers = {
        "x-api-key": API_KEY,
        "Accept": "application/json, text/event-stream",  # Combined Accept
    }
    url = f"{BASE_URL}/events/{TEAM_ID}"

    async with aiohttp.ClientSession(headers=headers) as session:
        while not stop_event.is_set():
            try:
                logger.info(f"Connecting to SSE: {url}")
                async with session.get(url, timeout=None) as response:
                    response.raise_for_status()

                    # Read line-by-line (More reliable than 'async for line in content')
                    while not stop_event.is_set():
                        raw_line = await response.content.readline()
                        if not raw_line:
                            break  # Connection closed

                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue

                        # ROBUST PARSING (The File 2 fix)
                        json_payload = None
                        if line.startswith("data:"):
                            content = line[5:].strip()
                            if content == "connected":
                                logger.info("Handshake: Connected")
                                continue
                            json_payload = content
                        elif line.startswith("{"):
                            json_payload = line

                        if json_payload:
                            await handle_sse_payload(json_payload)

            except Exception as e:
                logger.error(f"SSE Connection lost: {e}. Retrying in 5s...")
                await asyncio.sleep(5)


# --- Server Boilerplate ---


async def ws_handler(ws: ServerConnection):
    _ws_clients.add(ws)
    try:
        async for _ in ws:
            pass  # Keep alive
    finally:
        _ws_clients.discard(ws)


async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop_event.set)

    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        logger.info(f"Bridge Active! WS: ws://{WS_HOST}:{WS_PORT}")
        await listen_to_sse(stop_event)


if __name__ == "__main__":
    asyncio.run(main())
