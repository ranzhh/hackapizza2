"""
Hackapizza 2.0 — Real-Time Log Dashboard Server
================================================
FastAPI backend with REST + WebSocket for the monitoring dashboard.
Events-centric: uses the `events` table and its typed sub-tables.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg
import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_CONNSTR = os.getenv(
    "DASHBOARD_DB_URL",
    "postgresql://postgres:ragu<pizza@10.0.5.45:5432/ragu",
)
PORT = int(os.getenv("DASHBOARD_PORT", "3001"))

# ---------------------------------------------------------------------------
# DB pool
# ---------------------------------------------------------------------------
pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DB_CONNSTR, min_size=2, max_size=10)
    return pool


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self._last_event_id: int = 0

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Background poller
# ---------------------------------------------------------------------------
async def poll_updates():
    """Poll DB every 2s for new events, broadcast to WS clients."""
    p = await get_pool()
    row = await p.fetchrow("SELECT COALESCE(MAX(id), 0) AS mx FROM events")
    manager._last_event_id = row["mx"]

    while True:
        await asyncio.sleep(2)
        if not manager.active:
            continue
        try:
            ev_rows = await p.fetch(
                """SELECT id, timestamp_utc, turn_id, event_type, data_json
                   FROM events WHERE id > $1 ORDER BY id ASC LIMIT 100""",
                manager._last_event_id,
            )
            if ev_rows:
                manager._last_event_id = ev_rows[-1]["id"]
                events = []
                for r in ev_rows:
                    d = _row_to_dict(r)
                    raw = d.pop("data_json", None)
                    if raw:
                        try:
                            d["data"] = json.loads(raw)
                        except Exception:
                            d["data"] = raw
                    else:
                        d["data"] = {}
                    events.append(d)
                await manager.broadcast({"type": "new_events", "data": events})
        except Exception:
            pass


def _row_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    task = asyncio.create_task(poll_updates())
    yield
    task.cancel()
    if pool:
        await pool.close()


app = FastAPI(title="Hackapizza Dashboard", lifespan=lifespan)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def get_events(
    limit: int = Query(200, ge=1, le=1000),
    event_type: str | None = Query(None),
):
    p = await get_pool()
    cond, params = "", []
    idx = 1
    if event_type:
        cond = f" WHERE event_type = ${idx}"
        params.append(event_type)
        idx += 1
    params.extend([limit])
    rows = await p.fetch(
        f"""SELECT id, timestamp_utc, turn_id, event_type, data_json
            FROM events{cond} ORDER BY timestamp_utc DESC LIMIT ${idx}""",
        *params,
    )
    result = []
    for r in rows:
        d = _row_to_dict(r)
        raw = d.pop("data_json", None)
        if raw:
            try:
                d["data"] = json.loads(raw)
            except Exception:
                d["data"] = raw
        else:
            d["data"] = {}
        result.append(d)
    return {"events": result}


@app.get("/api/phase")
async def get_current_phase():
    """Return the most recent phase-change event."""
    p = await get_pool()
    row = await p.fetchrow(
        """SELECT id, timestamp_utc, turn_id, event_type, data_json
           FROM events
           WHERE event_type = 'game_phase_changed'
           ORDER BY timestamp_utc DESC LIMIT 1"""
    )
    if not row:
        return {"phase": "unknown", "turn_id": None, "timestamp": None}
    d = _row_to_dict(row)
    try:
        data = json.loads(d["data_json"])
    except Exception:
        data = {}
    return {
        "phase": data.get("phase", "unknown"),
        "turn_id": d.get("turn_id"),
        "timestamp": d.get("timestamp_utc"),
    }


@app.get("/api/clients")
async def get_clients(
    limit: int = Query(200, ge=1, le=1000),
):
    """Return client_spawned events with structured data."""
    p = await get_pool()
    rows = await p.fetch(
        """SELECT cs.id, cs.client_name, cs.order_text,
                  e.timestamp_utc, e.turn_id
           FROM event_client_spawned cs
           JOIN events e ON e.id = cs.event_id
           ORDER BY e.timestamp_utc DESC
           LIMIT $1""",
        limit,
    )
    return {"clients": [_row_to_dict(r) for r in rows]}


@app.get("/api/preparations")
async def get_preparations(
    limit: int = Query(200, ge=1, le=1000),
):
    """Return preparation_complete events with structured data."""
    p = await get_pool()
    rows = await p.fetch(
        """SELECT pc.id, pc.dish_name,
                  e.timestamp_utc, e.turn_id
           FROM event_preparation_complete pc
           JOIN events e ON e.id = pc.event_id
           ORDER BY e.timestamp_utc DESC
           LIMIT $1""",
        limit,
    )
    return {"preparations": [_row_to_dict(r) for r in rows]}


@app.get("/api/messages")
async def get_messages(
    limit: int = Query(200, ge=1, le=1000),
    sender_name: str | None = Query(None),
):
    """Return new_message events with structured data."""
    p = await get_pool()
    cond, params = "", []
    idx = 1
    if sender_name:
        cond = f" AND nm.sender_name ILIKE ${idx}"
        params.append(f"%{sender_name}%")
        idx += 1
    params.append(limit)
    rows = await p.fetch(
        f"""SELECT nm.id, nm.message_id, nm.sender_id, nm.sender_name,
                   nm.text, nm.message_datetime,
                   e.timestamp_utc, e.turn_id
            FROM event_new_message nm
            JOIN events e ON e.id = nm.event_id
            WHERE 1=1{cond}
            ORDER BY e.timestamp_utc DESC
            LIMIT ${idx}""",
        *params,
    )
    return {"messages": [_row_to_dict(r) for r in rows]}


@app.get("/api/restaurants")
async def get_restaurants():
    p = await get_pool()
    try:
        rows = await p.fetch(
            """SELECT r.id, r.call_id, r.restaurant_id, r.name, r.balance, r.reputation, r.is_open,
                      c.timestamp_utc, c.turn_id
               FROM restaurants r JOIN calls c ON c.id = r.call_id
               WHERE r.call_id = (SELECT MAX(call_id) FROM restaurants)
               ORDER BY r.balance DESC"""
        )
    except Exception:
        return {"restaurants": []}
    result = []
    for row in rows:
        d = _row_to_dict(row)
        inv = await p.fetch(
            "SELECT ingredient_name, quantity FROM restaurant_inventory WHERE restaurant_row_id = $1",
            row["id"],
        )
        d["inventory"] = {r["ingredient_name"]: r["quantity"] for r in inv}
        menu = await p.fetch(
            "SELECT name, price FROM restaurant_menu_items WHERE restaurant_row_id = $1",
            row["id"],
        )
        d["menu"] = [dict(r) for r in menu]
        result.append(d)
    return {"restaurants": result}


@app.get("/api/recipes")
async def get_recipes():
    p = await get_pool()
    rows = await p.fetch(
        "SELECT id, name, preparation_time_ms, prestige FROM recipes ORDER BY prestige DESC"
    )
    result = []
    for row in rows:
        d = _row_to_dict(row)
        ings = await p.fetch(
            "SELECT ingredient_name, quantity FROM recipe_ingredients WHERE recipe_id = $1",
            row["id"],
        )
        d["ingredients"] = [dict(r) for r in ings]
        result.append(d)
    return {"recipes": result}


@app.get("/api/stats")
async def get_stats():
    p = await get_pool()
    # Event-based stats
    ev_total = await p.fetchrow("SELECT COUNT(*) AS total FROM events")
    ev_last = await p.fetchrow("SELECT MAX(timestamp_utc) AS last_at FROM events")
    stats = {
        "total_events": ev_total["total"],
        "last_event_at": ev_last["last_at"].isoformat() if ev_last["last_at"] else None,
    }
    # Event counts by type
    ev_rows = await p.fetch(
        "SELECT event_type, COUNT(*) AS cnt FROM events GROUP BY event_type ORDER BY cnt DESC"
    )
    stats["by_event_type"] = [dict(r) for r in ev_rows]
    # Counts from typed tables
    client_count = await p.fetchrow("SELECT COUNT(*) AS cnt FROM event_client_spawned")
    prep_count = await p.fetchrow("SELECT COUNT(*) AS cnt FROM event_preparation_complete")
    msg_count = await p.fetchrow("SELECT COUNT(*) AS cnt FROM event_new_message")
    stats["client_spawned_count"] = client_count["cnt"]
    stats["preparation_complete_count"] = prep_count["cnt"]
    stats["new_message_count"] = msg_count["cnt"]
    # Turn IDs
    turn_rows = await p.fetch(
        "SELECT DISTINCT turn_id FROM events WHERE turn_id IS NOT NULL ORDER BY turn_id"
    )
    stats["turn_ids"] = [r["turn_id"] for r in turn_rows]
    return stats


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
static_dir = os.path.join(os.path.dirname(__file__), "public")


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))


app.mount("/", StaticFiles(directory=static_dir), name="static")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=True)
