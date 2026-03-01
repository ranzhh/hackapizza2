"""
Hackapizza 2.0 — Real-Time Log Dashboard Server
================================================
FastAPI backend with REST + WebSocket for the monitoring dashboard.
Events-centric: uses the `events` table and its typed sub-tables.

NEW in this version:
  - GET /api/turns          → list of distinct turn_ids that have a server bid message
  - GET /api/bids?turn_id=X → parsed bid matrix (restaurants × ingredients) for a turn
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import aiohttp
import asyncpg
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Load variables from .env in the project root (no-op if file is absent)
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(_project_root, ".env"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_CONNSTR = (
    os.getenv("HACKAPIZZA_SQL_CONNSTR")
    or os.getenv("DASHBOARD_DB_URL")
)
if not DB_CONNSTR:
    raise RuntimeError(
        "No database URL found. Set HACKAPIZZA_SQL_CONNSTR in your .env file "
        "or as an environment variable."
    )
PORT = int(os.getenv("DASHBOARD_PORT", "3001"))
TEAM_ID = os.getenv("HACKAPIZZA_TEAM_ID", "6")
TEAM_API_KEY = os.getenv("HACKAPIZZA_TEAM_API_KEY", "")
HACKAPIZZA_BASE_URL = "https://hackapizza.datapizza.tech"

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
                _EVENTS_SQL + " WHERE e.id > $1 ORDER BY e.id ASC LIMIT 100",
                manager._last_event_id,
            )
            if ev_rows:
                manager._last_event_id = ev_rows[-1]["id"]
                events = []
                for r in ev_rows:
                    d = _row_to_dict(r)
                    d["detail"] = _build_detail(r)
                    events.append(d)
                await manager.broadcast({"type": "new_events", "data": events})
        except Exception as exc:
            print(f"[poller] error: {exc}")
            pass


def _row_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Bid-message parser
# ---------------------------------------------------------------------------
# Matches blocks like:
#   "Restaurant 3 try to buy:2 Lacrime di Unicorno at single price of: 70 result:Bought 2 Lacrime di Unicorno for 140"
_BID_RE = re.compile(
    r"Restaurant\s+(\S+)\s+try to buy:(\d+)\s+(.+?)\s+at single price of:\s*(\d+)"
    r"\s+result:Bought\s+\d+\s+.+?\s+for\s+\d+"
)


def _parse_bids(text: str) -> list[dict]:
    """
    Parse a server bid message and return a flat list of bid records:
      { restaurant: str, ingredient: str, quantity: int, unit_price: int }
    """
    results = []
    for m in _BID_RE.finditer(text):
        restaurant, qty, ingredient, price = m.groups()
        results.append(
            {
                "restaurant": f"Restaurant {restaurant}",
                "ingredient": ingredient.strip(),
                "quantity": int(qty),
                "unit_price": int(price),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Shared SQL fragment: events + all sub-table LEFT JOINs
# Used by both the REST endpoint and the WebSocket poller.
# ---------------------------------------------------------------------------
_EVENTS_SQL = """
    SELECT
        e.id, e.timestamp_utc, e.turn_id, e.event_type,
        -- game_phase_changed
        gpc.phase,
        -- game_started
        gs.turn_id                  AS gs_turn_id,
        -- client_spawned
        cs.client_name,
        cs.order_text,
        -- preparation_complete
        pc.dish_name                AS pc_dish_name,
        -- new_message
        nm.sender_name,
        nm.text                     AS nm_text,
        -- mcp_closed_bid
        cb.bids_json,
        -- mcp_create_market_entry
        cme.side,
        cme.ingredient_name,
        cme.quantity                AS cme_quantity,
        cme.price                   AS cme_price,
        -- mcp_delete_market_entry
        dme.market_entry_id         AS dme_entry_id,
        -- mcp_execute_transaction
        et.market_entry_id          AS et_entry_id,
        -- mcp_prepare_dish
        pd.dish_name                AS pd_dish_name,
        -- mcp_save_menu
        sm.items_json,
        -- mcp_send_message
        snd.recipient_id,
        snd.text                    AS snd_text,
        -- mcp_serve_dish
        sd.dish_name                AS sd_dish_name,
        sd.client_id,
        -- mcp_set_open_status
        sos.is_open
    FROM events e
    LEFT JOIN event_game_phase_changed      gpc ON gpc.event_id = e.id
    LEFT JOIN event_game_started             gs  ON gs.event_id  = e.id
    LEFT JOIN event_client_spawned           cs  ON cs.event_id  = e.id
    LEFT JOIN event_preparation_complete     pc  ON pc.event_id  = e.id
    LEFT JOIN event_new_message              nm  ON nm.event_id  = e.id
    LEFT JOIN event_mcp_closed_bid           cb  ON cb.event_id  = e.id
    LEFT JOIN event_mcp_create_market_entry  cme ON cme.event_id = e.id
    LEFT JOIN event_mcp_delete_market_entry  dme ON dme.event_id = e.id
    LEFT JOIN event_mcp_execute_transaction  et  ON et.event_id  = e.id
    LEFT JOIN event_mcp_prepare_dish         pd  ON pd.event_id  = e.id
    LEFT JOIN event_mcp_save_menu            sm  ON sm.event_id  = e.id
    LEFT JOIN event_mcp_send_message         snd ON snd.event_id = e.id
    LEFT JOIN event_mcp_serve_dish           sd  ON sd.event_id  = e.id
    LEFT JOIN event_mcp_set_open_status      sos ON sos.event_id = e.id
"""


def _build_detail(row: asyncpg.Record) -> dict:
    """Extract the relevant sub-table fields for each event_type."""
    et = row["event_type"]
    if et == "game_phase_changed":
        return {"phase": row["phase"]}
    if et == "game_started":
        return {"turn_id": row["gs_turn_id"]}
    if et == "client_spawned":
        return {"client": row["client_name"], "order": row["order_text"]}
    if et == "preparation_complete":
        return {"dish": row["pc_dish_name"]}
    if et == "new_message":
        text = row["nm_text"] or ""
        return {"sender": row["sender_name"], "text": text[:200] + ("…" if len(text) > 200 else "")}
    if et == "mcp_closed_bid":
        try:
            return {"bids": json.loads(row["bids_json"])}
        except Exception:
            return {"bids_json": (row["bids_json"] or "")[:300]}
    if et == "mcp_create_market_entry":
        return {
            "side": row["side"],
            "ingredient": row["ingredient_name"],
            "qty": row["cme_quantity"],
            "price": row["cme_price"],
        }
    if et == "mcp_delete_market_entry":
        return {"market_entry_id": row["dme_entry_id"]}
    if et == "mcp_execute_transaction":
        return {"market_entry_id": row["et_entry_id"]}
    if et == "mcp_prepare_dish":
        return {"dish": row["pd_dish_name"]}
    if et == "mcp_save_menu":
        try:
            return {"items": json.loads(row["items_json"])}
        except Exception:
            return {"items_json": (row["items_json"] or "")[:300]}
    if et == "mcp_send_message":
        text = row["snd_text"] or ""
        return {"recipient_id": row["recipient_id"], "text": text[:200] + ("…" if len(text) > 200 else "")}
    if et == "mcp_serve_dish":
        return {"dish": row["sd_dish_name"], "client_id": row["client_id"]}
    if et == "mcp_set_open_status":
        return {"is_open": bool(row["is_open"])}
    return {}


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
# REST endpoints  (all original ones kept unchanged)
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def get_events(
    limit: int = Query(200, ge=1, le=1000),
    event_type: str | None = Query(None),
):
    p = await get_pool()
    if event_type:
        sql = _EVENTS_SQL + " WHERE e.event_type = $1 ORDER BY e.timestamp_utc DESC LIMIT $2"
        rows = await p.fetch(sql, event_type, limit)
    else:
        sql = _EVENTS_SQL + " ORDER BY e.timestamp_utc DESC LIMIT $1"
        rows = await p.fetch(sql, limit)

    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["detail"] = _build_detail(r)
        result.append(d)
    return {"events": result}


@app.get("/api/phase")
async def get_current_phase():
    """Return the most recent phase-change event, reading phase from the sub-table."""
    p = await get_pool()
    row = await p.fetchrow(
        """SELECT e.turn_id, e.timestamp_utc, gpc.phase
           FROM events e
           JOIN event_game_phase_changed gpc ON gpc.event_id = e.id
           ORDER BY e.timestamp_utc DESC
           LIMIT 1"""
    )
    if not row:
        return {"phase": "unknown", "turn_id": None, "timestamp": None}
    d = _row_to_dict(row)
    return {
        "phase": d.get("phase", "unknown"),
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
    ev_total = await p.fetchrow("SELECT COUNT(*) AS total FROM events")
    ev_last = await p.fetchrow("SELECT MAX(timestamp_utc) AS last_at FROM events")
    stats = {
        "total_events": ev_total["total"],
        "last_event_at": ev_last["last_at"].isoformat() if ev_last["last_at"] else None,
    }
    ev_rows = await p.fetch(
        "SELECT event_type, COUNT(*) AS cnt FROM events GROUP BY event_type ORDER BY cnt DESC"
    )
    stats["by_event_type"] = [dict(r) for r in ev_rows]
    client_count = await p.fetchrow("SELECT COUNT(*) AS cnt FROM event_client_spawned")
    prep_count = await p.fetchrow("SELECT COUNT(*) AS cnt FROM event_preparation_complete")
    msg_count = await p.fetchrow("SELECT COUNT(*) AS cnt FROM event_new_message")
    stats["client_spawned_count"] = client_count["cnt"]
    stats["preparation_complete_count"] = prep_count["cnt"]
    stats["new_message_count"] = msg_count["cnt"]
    turn_rows = await p.fetch(
        "SELECT DISTINCT turn_id FROM events WHERE turn_id IS NOT NULL ORDER BY turn_id"
    )
    stats["turn_ids"] = [r["turn_id"] for r in turn_rows]
    return stats


# ---------------------------------------------------------------------------
# NEW: Market Bids endpoints
# ---------------------------------------------------------------------------

def _coerce_turn_id(turn_id_str: str | None, db_turn_ids: list) -> Any:
    """
    The turn_id column may be stored as INT in Postgres.
    Try to cast the incoming string to the same type as the existing values
    so the WHERE clause doesn't fail due to a type mismatch.
    """
    if turn_id_str is None:
        return None
    if db_turn_ids and isinstance(db_turn_ids[0], int):
        try:
            return int(turn_id_str)
        except (ValueError, TypeError):
            pass
    return turn_id_str


@app.get("/api/turns")
async def get_bid_turns():
    """
    Return all distinct turn_ids that contain at least one server bid message
    (event_type = 'new_message', sender_name = 'server', text contains bid data).
    Falls back to returning ALL distinct turn_ids if none have bid messages.
    Always returns {"turn_ids": [...]} — never a 500.
    """
    p = await get_pool()
    turn_ids: list = []

    # Primary: turns that actually have server bid messages
    try:
        rows = await p.fetch(
            """
            SELECT DISTINCT e.turn_id
            FROM event_new_message nm
            JOIN events e ON e.id = nm.event_id
            WHERE LOWER(nm.sender_name) = 'server'
              AND nm.text ILIKE '%try to buy%'
              AND e.turn_id IS NOT NULL
            ORDER BY e.turn_id
            """
        )
        turn_ids = [r["turn_id"] for r in rows]
    except Exception as exc:
        # Column name / schema mismatch — log and continue to fallback
        print(f"[turns] primary query failed: {exc}")

    # Fallback 1: any message from 'server' that looks like bid data
    if not turn_ids:
        try:
            rows = await p.fetch(
                """
                SELECT DISTINCT e.turn_id
                FROM event_new_message nm
                JOIN events e ON e.id = nm.event_id
                WHERE nm.text ILIKE '%try to buy%'
                  AND e.turn_id IS NOT NULL
                ORDER BY e.turn_id
                """
            )
            turn_ids = [r["turn_id"] for r in rows]
        except Exception as exc:
            print(f"[turns] fallback-1 query failed: {exc}")

    # Fallback 2: just return every turn that exists
    if not turn_ids:
        try:
            rows = await p.fetch(
                "SELECT DISTINCT turn_id FROM events WHERE turn_id IS NOT NULL ORDER BY turn_id"
            )
            turn_ids = [r["turn_id"] for r in rows]
        except Exception as exc:
            print(f"[turns] fallback-2 query failed: {exc}")

    return {"turn_ids": turn_ids}


@app.get("/api/bids")
async def get_bids(turn_id: str | None = Query(None)):
    """
    Parse all server bid messages for a given turn and return a structured matrix.

    Response shape:
    {
      "turn_id": ...,
      "restaurants": ["Restaurant 3", ...],
      "ingredients": ["Lacrime di Unicorno", ...],
      "bids": {
        "Lacrime di Unicorno": {
          "Restaurant 3": {"unit_price": 70, "quantity": 2},
          ...
        }
      },
      "error": null   // populated if something went wrong
    }
    """
    p = await get_pool()
    empty = {"turn_id": turn_id, "restaurants": [], "ingredients": [], "bids": {}, "error": None}

    # ------------------------------------------------------------------ #
    # Helper: fetch bid-message rows for a specific turn value            #
    # ------------------------------------------------------------------ #
    async def _fetch_for_turn(tv: Any) -> list:
        """Try both 'server'-filtered and unfiltered queries."""
        for sender_cond in (
            "AND LOWER(nm.sender_name) = 'server'",
            "",  # no sender filter – wider net
        ):
            try:
                return await p.fetch(
                    f"""
                    SELECT nm.text, e.turn_id
                    FROM event_new_message nm
                    JOIN events e ON e.id = nm.event_id
                    WHERE nm.text ILIKE '%try to buy%'
                      {sender_cond}
                      AND e.turn_id = $1
                    ORDER BY e.timestamp_utc ASC
                    """,
                    tv,
                )
            except Exception as exc:
                print(f"[bids] fetch_for_turn(sender_cond={sender_cond!r}) failed: {exc}")
        return []

    # ------------------------------------------------------------------ #
    # Resolve turn value                                                  #
    # ------------------------------------------------------------------ #
    if turn_id is not None:
        # Detect DB column type by sampling existing turn_ids
        try:
            sample = await p.fetchrow(
                "SELECT turn_id FROM events WHERE turn_id IS NOT NULL LIMIT 1"
            )
            db_sample = [sample["turn_id"]] if sample else []
        except Exception:
            db_sample = []

        turn_val = _coerce_turn_id(turn_id, db_sample)
        rows = await _fetch_for_turn(turn_val)

    else:
        # No turn specified — find the latest turn with bid messages
        rows = []
        for sender_cond in (
            "AND LOWER(nm.sender_name) = 'server'",
            "",
        ):
            try:
                latest = await p.fetchrow(
                    f"""
                    SELECT e.turn_id
                    FROM event_new_message nm
                    JOIN events e ON e.id = nm.event_id
                    WHERE nm.text ILIKE '%try to buy%'
                      {sender_cond}
                      AND e.turn_id IS NOT NULL
                    ORDER BY e.timestamp_utc DESC
                    LIMIT 1
                    """
                )
                if latest:
                    rows = await _fetch_for_turn(latest["turn_id"])
                    if rows:
                        break
            except Exception as exc:
                print(f"[bids] latest-turn query failed: {exc}")

    if not rows:
        return {**empty, "error": "No bid messages found for this turn"}

    actual_turn_id = rows[0]["turn_id"]

    # ------------------------------------------------------------------ #
    # Parse bid entries                                                   #
    # ------------------------------------------------------------------ #
    all_bids: list[dict] = []
    for row in rows:
        parsed = _parse_bids(row["text"])
        all_bids.extend(parsed)

    if not all_bids:
        return {
            **empty,
            "turn_id": actual_turn_id,
            "error": "Messages found but no bids could be parsed (check message format)",
        }

    # ------------------------------------------------------------------ #
    # Build matrix                                                        #
    # ------------------------------------------------------------------ #
    restaurants: set[str] = set()
    ingredients: set[str] = set()
    for bid in all_bids:
        restaurants.add(bid["restaurant"])
        ingredients.add(bid["ingredient"])

    sorted_restaurants = sorted(
        restaurants,
        key=lambda r: int(r.split()[-1]) if r.split()[-1].isdigit() else r,
    )
    sorted_ingredients = sorted(ingredients)

    matrix: dict[str, dict[str, dict | None]] = {
        ing: {rest: None for rest in sorted_restaurants}
        for ing in sorted_ingredients
    }
    for bid in all_bids:
        matrix[bid["ingredient"]][bid["restaurant"]] = {
            "unit_price": bid["unit_price"],
            "quantity": bid["quantity"],
        }

    return {
        "turn_id": actual_turn_id,
        "restaurants": sorted_restaurants,
        "ingredients": sorted_ingredients,
        "bids": matrix,
        "error": None,
    }


# ---------------------------------------------------------------------------
# NEW: Balance & Reputation history + Price comparison
# ---------------------------------------------------------------------------

@app.get("/api/balance-history")
async def get_balance_history():
    """Return balance over time for our restaurant, plus summary stats."""
    p = await get_pool()
    rows = await p.fetch(
        """SELECT r.balance, c.timestamp_utc, c.turn_id
           FROM restaurants r
           JOIN calls c ON c.id = r.call_id
           WHERE r.restaurant_id = $1
           ORDER BY c.timestamp_utc ASC""",
        TEAM_ID,
    )
    points = [_row_to_dict(r) for r in rows]
    summary = _compute_summary(points, "balance")
    return {"points": points, "summary": summary}


@app.get("/api/reputation-history")
async def get_reputation_history():
    """Return reputation over time for our restaurant, plus summary stats."""
    p = await get_pool()
    rows = await p.fetch(
        """SELECT r.reputation, c.timestamp_utc, c.turn_id
           FROM restaurants r
           JOIN calls c ON c.id = r.call_id
           WHERE r.restaurant_id = $1
           ORDER BY c.timestamp_utc ASC""",
        TEAM_ID,
    )
    points = [_row_to_dict(r) for r in rows]
    summary = _compute_summary(points, "reputation")
    return {"points": points, "summary": summary}


def _compute_summary(points: list[dict], key: str) -> dict:
    """Compute aggregate stats: current value, last-turn change, min, max."""
    if not points:
        return {"current": None, "last_change": None, "min": None, "max": None, "start": None}
    values = [p[key] for p in points]
    current = values[-1]
    start = values[0]
    # Find last turn change: difference between last two distinct turn_ids
    last_change = None
    seen_turns: list[tuple] = []  # (turn_id, last_value_in_turn)
    prev_turn = object()  # sentinel that won't match any real turn_id
    for p in points:
        t = p.get("turn_id")
        if t != prev_turn:
            seen_turns.append((t, p[key]))
            prev_turn = t
        elif seen_turns:
            seen_turns[-1] = (t, p[key])
    if len(seen_turns) >= 2:
        last_change = seen_turns[-1][1] - seen_turns[-2][1]
    return {
        "current": current,
        "last_change": round(last_change, 2) if last_change is not None else None,
        "min": min(values),
        "max": max(values),
        "start": start,
    }


# Cache for competitor menus (refreshed every 60s)
_comp_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_COMP_CACHE_TTL = 60  # seconds


async def _fetch_our_menu_from_db() -> dict[str, float | None]:
    """Read our latest menu prices from the DB (restaurant_menu_items)."""
    p = await get_pool()
    rows = await p.fetch(
        """SELECT rmi.name, rmi.price
           FROM restaurant_menu_items rmi
           JOIN restaurants r ON r.id = rmi.restaurant_row_id
           WHERE r.restaurant_id = $1
             AND r.call_id = (
               SELECT MAX(r2.call_id) FROM restaurants r2 WHERE r2.restaurant_id = $1
             )""",
        TEAM_ID,
    )
    return {r["name"]: r["price"] for r in rows if r["name"]}


async def _fetch_competitor_menus() -> list[dict]:
    """Fetch only competitor menus from the external API."""
    headers = {"x-api-key": TEAM_API_KEY}
    competitors: list[dict] = []
    our_rid = str(TEAM_ID)

    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=25)
    connector = aiohttp.TCPConnector(limit=3, force_close=True)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Get restaurant list
        restaurant_list: list[dict] = []
        try:
            async with session.get(
                f"{HACKAPIZZA_BASE_URL}/restaurants", headers=headers,
            ) as resp:
                if resp.status == 200:
                    restaurant_list = await resp.json()
        except Exception as exc:
            print(f"[price-comparison] /restaurants failed: {exc}")

        name_map: dict[str, str] = {}
        for r in restaurant_list:
            rid = str(r.get("id", ""))
            if rid:
                name_map[rid] = r.get("name", f"Restaurant {rid}")

        comp_ids = [rid for rid in (name_map.keys() or [str(i) for i in range(1, 26)]) if rid != our_rid]

        for rid in comp_ids:
            try:
                url = f"{HACKAPIZZA_BASE_URL}/restaurant/{rid}/menu"
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    items = data.get("items", []) if isinstance(data, dict) else data
                    if isinstance(items, list) and items:
                        competitors.append({
                            "restaurant_id": rid,
                            "restaurant_name": name_map.get(rid, f"Restaurant {rid}"),
                            "menu": items,
                        })
            except Exception as exc:
                print(f"[price-comparison] R.{rid} failed: {exc}")

    return competitors


@app.get("/api/price-comparison")
async def get_price_comparison():
    """Our menu from DB + competitor menus from external API (cached 60s)."""
    # Always read our menu fresh from DB (fast local query)
    our_menu = await _fetch_our_menu_from_db()

    # Competitor data is cached
    now = time.time()
    if _comp_cache["data"] is None or (now - _comp_cache["ts"]) >= _COMP_CACHE_TTL:
        _comp_cache["data"] = await _fetch_competitor_menus()
        _comp_cache["ts"] = now

    return {"our_menu": our_menu, "our_id": TEAM_ID, "competitors": _comp_cache["data"]}


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