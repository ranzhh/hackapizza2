from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import emergency_close


class FakeClient:
    def __init__(self, statuses: list[bool]):
        self.team_id = 99
        self._statuses = statuses
        self._status_idx = 0
        self.calls: list[tuple[str, object]] = []

    async def get_my_restaurant(self):
        value = self._statuses[self._status_idx]
        self._status_idx += 1
        self.calls.append(("get_my_restaurant", value))
        return SimpleNamespace(is_open=value)

    async def set_restaurant_open_status(self, is_open: bool):
        self.calls.append(("set_restaurant_open_status", is_open))
        return {"ok": True}


@pytest.mark.asyncio
async def test_emergency_close_restaurant_forces_close_and_returns_typed_result():
    client = FakeClient(statuses=[True, False])

    result = await emergency_close.emergency_close_restaurant(client)  # type: ignore[arg-type]

    assert result.team_id == 99
    assert result.was_open is True
    assert result.is_open is False
    assert result.action_response == {"ok": True}
    assert client.calls == [
        ("get_my_restaurant", True),
        ("set_restaurant_open_status", False),
        ("get_my_restaurant", False),
    ]


@pytest.mark.asyncio
async def test_run_emergency_close_uses_client_and_session(monkeypatch: pytest.MonkeyPatch):
    created: dict[str, object] = {}

    class FakeHackapizzaClient:
        def __init__(self, team_id, api_key, base_url):
            self.team_id = 123
            self.api_key = api_key
            self.base_url = base_url
            self._headers = {"x-api-key": "k"}
            self._session = None
            created["ctor"] = (team_id, api_key, base_url)

    class FakeSession:
        def __init__(self, *, timeout, headers):
            created["session_args"] = {"timeout": timeout, "headers": headers}

        async def __aenter__(self):
            created["entered"] = True
            return "session-object"

        async def __aexit__(self, exc_type, exc, tb):
            created["exited"] = True
            return False

    async def fake_emergency_close_restaurant(client):
        created["client_session_inside"] = client._session
        return emergency_close.EmergencyCloseResult(
            team_id=client.team_id,
            was_open=True,
            is_open=False,
            action_response={"ok": True},
        )

    monkeypatch.setattr(emergency_close, "HackapizzaClient", FakeHackapizzaClient)
    monkeypatch.setattr(emergency_close.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(
        emergency_close,
        "emergency_close_restaurant",
        fake_emergency_close_restaurant,
    )

    result = await emergency_close.run_emergency_close(
        team_id=7,
        api_key="abc",
        base_url="https://example.test",
        timeout_seconds=5.0,
    )

    assert result.is_open is False
    assert created["ctor"] == (7, "abc", "https://example.test")
    assert created["entered"] is True
    assert created["exited"] is True
    assert created["client_session_inside"] == "session-object"
