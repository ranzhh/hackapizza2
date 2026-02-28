"""Quick script to list open restaurants.

Usage:
  uv run python list_open_restaurants.py
"""

from __future__ import annotations

import asyncio

import aiohttp

from hp2.core.api import HackapizzaClient


async def main() -> None:
    client = HackapizzaClient(team_id=None, api_key=None)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=client._headers) as session:
        client._session = session
        restaurants = await client.get_restaurants()

    open_restaurants = [r for r in restaurants if r.is_open]

    if not open_restaurants:
        print("No open restaurants right now.")
        return

    print(f"Open restaurants ({len(open_restaurants)}):")
    for restaurant in open_restaurants:
        print(f"- id={restaurant.id} name={restaurant.name}")


if __name__ == "__main__":
    asyncio.run(main())
