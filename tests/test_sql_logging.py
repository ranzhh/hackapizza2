import asyncio
import sqlite3
from pathlib import Path

import pytest

from hp2.core.api import HackapizzaClient
from hp2.core.sql_logging_mixin import SqlLoggingMixin


class _MixinHarness(SqlLoggingMixin):
    pass


class _FakeResponse:
    def __init__(self, payload=None, error: Exception | None = None, status: int = 200):
        self._payload = payload if payload is not None else {}
        self._error = error
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self._error:
            raise self._error

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, get_response: _FakeResponse | None = None, post_response: _FakeResponse | None = None):
        self._get_response = get_response
        self._post_response = post_response

    def get(self, url: str):
        assert url
        return self._get_response

    def post(self, url: str, json):
        assert url
        assert json
        return self._post_response


def _read_calls(db_path: Path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT source, name, status, turn_id, error_type, error_message FROM calls ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def _count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return int(count)


def test_sql_logging_mixin_creates_db_and_inserts_metadata(tmp_path: Path):
    db_path = tmp_path / "calls.db"
    harness = _MixinHarness()

    harness._init_sql_logging(str(db_path))
    harness._log_call_metadata(
        source="http_get",
        name="/recipes",
        status="ok",
        duration_ms=12.3,
        turn_id=None,
    )
    harness._close_sql_logging()

    assert db_path.exists()
    rows = _read_calls(db_path)
    assert len(rows) == 1
    assert rows[0][:3] == ("http_get", "/recipes", "ok")


def test_http_get_and_mcp_call_are_logged(tmp_path: Path):
    db_path = tmp_path / "calls.db"
    client = HackapizzaClient(
        team_id=6,
        api_key="test-key",
        enable_sql_logging=True,
        log_db_path=str(db_path),
    )

    client._session = _FakeSession(get_response=_FakeResponse(payload={"ok": True}))
    result = asyncio.run(client._http_get("/meals?turn_id=99&restaurant_id=6"))
    assert result == {"ok": True}

    client._session = _FakeSession(post_response=_FakeResponse(payload={"result": {"value": "ok"}}))
    mcp_result = asyncio.run(client._mcp_call("save_menu", items=[]))
    assert mcp_result == {"value": "ok"}

    client._close_sql_logging()

    rows = _read_calls(db_path)
    assert len(rows) == 2
    assert rows[0][:4] == ("http_get", "/meals?turn_id=99&restaurant_id=6", "ok", "99")
    assert rows[1][:3] == ("mcp_call", "save_menu", "ok")


def test_http_get_error_is_logged_and_reraised(tmp_path: Path):
    db_path = tmp_path / "calls.db"
    client = HackapizzaClient(
        team_id=6,
        api_key="test-key",
        enable_sql_logging=True,
        log_db_path=str(db_path),
    )

    client._session = _FakeSession(
        get_response=_FakeResponse(error=RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(client._http_get("/bid_history?turn_id=7"))

    client._close_sql_logging()
    rows = _read_calls(db_path)
    assert len(rows) == 1
    assert rows[0][:4] == ("http_get", "/bid_history?turn_id=7", "error", "7")
    assert rows[0][4] == "RuntimeError"


def test_get_recipes_persists_typed_rows(tmp_path: Path):
    db_path = tmp_path / "calls.db"
    client = HackapizzaClient(
        team_id=6,
        api_key="test-key",
        enable_sql_logging=True,
        log_db_path=str(db_path),
    )

    recipes_payload = [
        {
            "name": "Nebulosa Galattica",
            "preparationTimeMs": 3000,
            "ingredients": {
                "Radici di Gravità": 1,
                "Alghe Bioluminescenti": 2,
            },
            "prestige": 31,
        },
        {
            "name": "Cosmic Harmony",
            "preparationTimeMs": 3200,
            "ingredients": {
                "Pane di Luce": 1,
            },
            "prestige": 40,
        },
    ]

    client._session = _FakeSession(get_response=_FakeResponse(payload=recipes_payload))
    typed = asyncio.run(client.get_recipes())

    assert len(typed) == 2
    client._close_sql_logging()

    assert _count_rows(db_path, "calls") == 1
    assert _count_rows(db_path, "recipes") == 2
    assert _count_rows(db_path, "recipe_ingredients") == 3


def test_get_restaurants_persists_typed_rows(tmp_path: Path):
    db_path = tmp_path / "calls.db"
    client = HackapizzaClient(
        team_id=6,
        api_key="test-key",
        enable_sql_logging=True,
        log_db_path=str(db_path),
    )

    restaurants_payload = [
        {
            "id": "6",
            "name": "RAGù",
            "balance": 1000,
            "inventory": {
                "Radici di Gravità": 2,
                "Alghe Bioluminescenti": 1,
            },
            "reputation": 100,
            "isOpen": True,
            "kitchen": [],
            "menu": {
                "items": [
                    {"name": "Nebulosa Galattica", "price": 42.0},
                    {"name": "Cosmic Harmony", "price": 30.0},
                ]
            },
            "receivedMessages": [],
        },
        {
            "id": "7",
            "name": "Other Team",
            "balance": 950,
            "inventory": {},
            "reputation": 97,
            "isOpen": False,
            "kitchen": [],
            "menu": {"items": []},
            "receivedMessages": [],
        },
    ]

    client._session = _FakeSession(get_response=_FakeResponse(payload=restaurants_payload))
    typed = asyncio.run(client.get_restaurants())

    assert len(typed) == 2
    client._close_sql_logging()

    assert _count_rows(db_path, "calls") == 1
    assert _count_rows(db_path, "restaurants") == 2
    assert _count_rows(db_path, "restaurant_inventory") == 2
    assert _count_rows(db_path, "restaurant_menu_items") == 2


def test_remaining_endpoints_persist_typed_rows(tmp_path: Path):
    db_path = tmp_path / "calls.db"
    client = HackapizzaClient(
        team_id=6,
        api_key="test-key",
        enable_sql_logging=True,
        log_db_path=str(db_path),
    )

    meals_payload = [{"clientName": "A", "orderText": "Nebulosa"}]
    client._session = _FakeSession(get_response=_FakeResponse(payload=meals_payload))
    meals = asyncio.run(client.get_meals("42"))
    assert len(meals) == 1

    market_payload = [{"id": "m1", "ingredient": "Alghe", "price": 10, "quantity": 2}]
    client._session = _FakeSession(get_response=_FakeResponse(payload=market_payload))
    market_entries = asyncio.run(client.get_market_entries())
    assert len(market_entries) == 1

    bids_payload = [{"teamId": "6", "ingredient": "Alghe", "bid": 5.0, "quantity": 1}]
    client._session = _FakeSession(get_response=_FakeResponse(payload=bids_payload))
    bid_history = asyncio.run(client.get_bid_history("42"))
    assert len(bid_history) == 1

    client._close_sql_logging()

    assert _count_rows(db_path, "calls") == 3
    assert _count_rows(db_path, "meals") == 1
    assert _count_rows(db_path, "market_entries") == 1
    assert _count_rows(db_path, "bid_history") == 1
