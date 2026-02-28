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
