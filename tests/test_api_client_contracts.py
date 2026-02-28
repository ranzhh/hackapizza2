import json
import os
from typing import Any

import aiohttp
import pytest
from pydantic import BaseModel

from hp2.core.api import HackapizzaClient
from hp2.core.schema import (
    BidHistoryEntrySchema,
    MarketEntrySchema,
    MealSchema,
    MenuSchema,
    RecipeSchema,
    RestaurantSchema,
)
from hp2.core.settings import get_settings


def _to_json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    if isinstance(value, list):
        return [_to_json(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items()}
    return value


def _schema_keys(schema: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in schema.model_fields.items()}


def _assert_exact_keys(payload: dict[str, Any], expected: set[str]) -> None:
    actual = set(payload.keys())
    assert actual == expected, f"Expected keys {expected}, got {actual}. Payload={payload}"


def _extract_turn_id(payload: dict[str, Any]) -> str | None:
    for key in ("currentTurnId", "current_turn_id", "turnId", "turn_id"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


@pytest.fixture(scope="module")
def integration_enabled() -> None:
    if os.getenv("RUN_INTEGRATION_CONTRACTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_CONTRACTS=1 to run real API integration contracts")


@pytest.fixture(scope="module")
async def integration_client(integration_enabled: None) -> HackapizzaClient:
    try:
        _ = get_settings()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Missing settings for integration tests: {exc}")

    base_url = os.getenv("HACKAPIZZA_BASE_URL", "https://hackapizza.datapizza.tech")
    client = HackapizzaClient(
        team_id=None,
        api_key=None,
        base_url=base_url,
        enable_sql_logging=False,
    )
    session = aiohttp.ClientSession(headers=client._headers)
    client._session = session
    try:
        yield client
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_api_methods_return_json_matching_schema_properties_exactly(
    integration_client: HackapizzaClient,
):
    client = integration_client

    my_restaurant = _to_json(await client.get_my_restaurant())
    _assert_exact_keys(my_restaurant, _schema_keys(RestaurantSchema))

    restaurants = _to_json(await client.get_restaurants())
    assert isinstance(restaurants, list) and restaurants
    _assert_exact_keys(restaurants[0], _schema_keys(RestaurantSchema))

    recipes = _to_json(await client.get_recipes())
    assert isinstance(recipes, list) and recipes
    _assert_exact_keys(recipes[0], _schema_keys(RecipeSchema))

    my_menu = _to_json(await client.get_my_menu())
    _assert_exact_keys(my_menu, _schema_keys(MenuSchema))

    market_entries = _to_json(await client.get_market_entries())
    if market_entries:
        _assert_exact_keys(market_entries[0], _schema_keys(MarketEntrySchema))

    turn_id = (
        os.getenv("HACKAPIZZA_TEST_TURN_ID")
        or _extract_turn_id(my_restaurant)
        or _extract_turn_id(restaurants[0])
    )
    if turn_id is None:
        pytest.skip("No turn id available for meals/bid_history; set HACKAPIZZA_TEST_TURN_ID")

    meals = _to_json(await client.get_meals(turn_id))
    if meals:
        _assert_exact_keys(meals[0], _schema_keys(MealSchema))

    bid_history = _to_json(await client.get_bid_history(turn_id))
    if bid_history:
        _assert_exact_keys(bid_history[0], _schema_keys(BidHistoryEntrySchema))


@pytest.mark.asyncio
async def test_get_meals_raw_returns_actual_untyped_json_payload(
    integration_client: HackapizzaClient,
):
    client = integration_client
    my_restaurant = _to_json(await client.get_my_restaurant())
    turn_id = os.getenv("HACKAPIZZA_TEST_TURN_ID") or _extract_turn_id(my_restaurant)
    if turn_id is None:
        pytest.skip("No turn id available for get_meals_raw; set HACKAPIZZA_TEST_TURN_ID")

    payload = await client.get_meals_raw(turn_id)
    assert isinstance(payload, (dict, list))

    # Ensure it's true raw JSON by round-tripping through json serialization
    json.dumps(payload)
