"""Fetch restaurant menus and compute per-dish frequency & average price.

Usage as a module:
    from tools.api_unused_recipices import get_dish_stats
    stats = asyncio.run(get_dish_stats())

Or standalone:
    uv run python -m tools.api_unused_recipices
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import aiohttp

from hp2.core.api import HackapizzaClient
from hp2.core.schema import MenuSchema, RestaurantSchema
from pydantic import TypeAdapter

_MENU_ADAPTER = TypeAdapter(MenuSchema)


async def _fetch_menu(
    client: HackapizzaClient,
    restaurant: RestaurantSchema,
) -> tuple[RestaurantSchema, MenuSchema | None, str | None]:
    """Return (restaurant, menu, error) for a single restaurant."""
    endpoint = f"/restaurant/{restaurant.id}/menu"
    try:
        payload = await client._http_get(endpoint)
        menu = _MENU_ADAPTER.validate_python(payload)
        return restaurant, menu, None
    except Exception as exc:
        return restaurant, None, str(exc)


async def get_dish_stats() -> dict[str, dict]:
    """Fetch all open-restaurant menus and return per-dish statistics.

    Returns a dict keyed by dish name::

        {
            "Nebulosa Galattica": {
                "times_on_menu": 3,
                "avg_price": 42.5,
            },
            ...
        }
    """
    client = HackapizzaClient(team_id=None, api_key=None, enable_sql_logging=False)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=client._headers) as session:
        client._session = session

        restaurants = await client.get_restaurants()
        open_restaurants = [r for r in restaurants if r.is_open]

        if not open_restaurants:
            return {}

        tasks = [_fetch_menu(client, r) for r in open_restaurants]
        results = await asyncio.gather(*tasks)

    # Aggregate counts and prices per dish name
    counts: dict[str, int] = defaultdict(int)
    prices: dict[str, list[float]] = defaultdict(list)

    for _restaurant, menu, error in results:
        if error or menu is None:
            continue
        for item in menu.items:
            counts[item.name] += 1
            if item.price is not None:
                prices[item.name].append(item.price)

    dish_stats: dict[str, dict] = {}
    for name in sorted(counts, key=counts.get, reverse=True):  # type: ignore[arg-type]
        p = prices[name]
        dish_stats[name] = {
            "times_on_menu": counts[name],
            "avg_price": round(sum(p) / len(p), 2) if p else None,
        }

    return dish_stats


if __name__ == "__main__":
    import json

    stats = asyncio.run(get_dish_stats())
    print(json.dumps(stats, indent=2, ensure_ascii=False))
