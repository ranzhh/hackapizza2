import json
from pathlib import Path

import pytest

from hp2.core.api import BidRequest, MenuItem
from tools.discovery_api import DISCOVERY_ENDPOINTS, run_discovery


class FakeClient:
    def __init__(self):
        self.team_id = 42
        self.base_url = "https://example.test"
        self._headers = {"x-api-key": "test", "Content-Type": "application/json"}
        self._session = object()  # prevent harness from creating a real aiohttp session

        self.calls: list[str] = []

    async def get_my_restaurant(self):
        self.calls.append("get_my_restaurant")
        return {"currentTurnId": "turn-1"}

    async def get_restaurants(self):
        self.calls.append("get_restaurants")
        return [{"id": 42}, {"id": 99}]

    async def get_recipes(self):
        self.calls.append("get_recipes")
        return [{"name": "margherita", "ingredients": ["tomato"]}]

    async def get_my_menu(self):
        self.calls.append("get_my_menu")
        return [{"name": "margherita", "price": 10.0}]

    async def get_market_entries(self):
        self.calls.append("get_market_entries")
        return [{"id": 77}]

    async def get_meals(self, turn_id: str):
        self.calls.append("get_meals")
        assert turn_id == "turn-1"
        return [{"clientId": "client-123"}]

    async def get_bid_history(self, turn_id: str):
        self.calls.append("get_bid_history")
        assert turn_id == "turn-1"
        return []

    async def submit_closed_bids(self, bids: list[BidRequest]):
        self.calls.append("submit_closed_bids")
        assert bids[0].ingredient == "tomato"
        return {"ok": True}

    async def save_menu(self, items: list[MenuItem]):
        self.calls.append("save_menu")
        assert items[0].name == "margherita"
        return {"ok": True}

    async def create_market_entry(self, side, ingredient_name: str, quantity: int, price: float):
        self.calls.append("create_market_entry")
        assert ingredient_name == "tomato"
        assert quantity == 1
        assert price == 1.0
        return {"id": 501}

    async def execute_transaction(self, market_entry_id: int):
        self.calls.append("execute_transaction")
        assert market_entry_id == 77
        return {"ok": True}

    async def delete_market_entry(self, market_entry_id: int):
        self.calls.append("delete_market_entry")
        assert market_entry_id == 77
        return {"ok": True}

    async def prepare_dish(self, dish_name: str):
        self.calls.append("prepare_dish")
        assert dish_name == "margherita"
        return {"ok": True}

    async def serve_dish(self, dish_name: str, client_id: str):
        self.calls.append("serve_dish")
        assert dish_name == "margherita"
        assert client_id == "client-123"
        return {"ok": True}

    async def set_restaurant_open_status(self, is_open: bool):
        self.calls.append("set_restaurant_open_status")
        assert is_open is True
        return {"ok": True}

    async def send_direct_message(self, recipient_id: int, text: str):
        self.calls.append("send_direct_message")
        assert recipient_id == 99
        assert "Automated endpoint discovery ping" in text
        return {"ok": True}


@pytest.mark.asyncio
async def test_run_discovery_calls_all_endpoints_and_persists_report(tmp_path: Path):
    client = FakeClient()

    report_path = await run_discovery(
        client,  # type: ignore
        output_dir=tmp_path,
        include_actions=True,
        manage_session=False,
    )

    assert report_path.exists()
    assert report_path.parent == tmp_path
    assert (tmp_path / "latest.json").exists()

    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_data["summary"]["total"] == len(DISCOVERY_ENDPOINTS)
    assert report_data["summary"]["ok"] == len(DISCOVERY_ENDPOINTS)
    assert report_data["summary"]["error"] == 0
    assert [item["endpoint"] for item in report_data["results"]] == DISCOVERY_ENDPOINTS

    assert client.calls == DISCOVERY_ENDPOINTS


@pytest.mark.asyncio
async def test_run_discovery_is_stable_across_repeated_runs(tmp_path: Path):
    client = FakeClient()

    first = await run_discovery(
        client,  # type: ignore
        output_dir=tmp_path,
        include_actions=False,
        manage_session=False,
    )
    second = await run_discovery(
        client,  # type: ignore
        output_dir=tmp_path,
        include_actions=False,
        manage_session=False,
    )

    assert first.exists()
    assert second.exists()

    latest_data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest_data["include_actions"] is False

    expected = DISCOVERY_ENDPOINTS[:7]  # read-only endpoints
    assert [item["endpoint"] for item in latest_data["results"]] == expected
    assert latest_data["summary"]["total"] == len(expected)
