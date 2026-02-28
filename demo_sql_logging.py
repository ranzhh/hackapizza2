"""Small runnable demo for SQL logging.

Usage examples:
  uv run python demo_sql_logging.py --call recipes
  uv run python demo_sql_logging.py --call my_restaurant --db-path artifacts/calls_demo.db
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from pathlib import Path
from typing import Literal

import aiohttp

from hp2.core.api import HackapizzaClient


CallName = Literal["recipes", "my_restaurant", "restaurants"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demonstrate Hackapizza SQL logging")
    parser.add_argument(
        "--call",
        choices=["recipes", "my_restaurant", "restaurants"],
        default="recipes",
        help="Which SDK call to execute",
    )
    parser.add_argument(
        "--db-path",
        default="artifacts/calls_demo.db",
        help="Path to SQLite log database",
    )
    parser.add_argument(
        "--base-url",
        default="https://hackapizza.datapizza.tech",
        help="Hackapizza API base URL",
    )
    parser.add_argument("--team-id", type=int, default=None, help="Team ID (optional, read from .env if omitted)")
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Team API key (optional, read from .env if omitted)",
    )
    return parser.parse_args()


async def _execute_call(client: HackapizzaClient, call_name: CallName) -> None:
    if call_name == "recipes":
        recipes = await client.get_recipes()
        print(f"Fetched recipes: {len(recipes)}")
        return

    if call_name == "my_restaurant":
        restaurant = await client.get_my_restaurant()
        print(f"Fetched restaurant: {restaurant.name} (id={restaurant.id})")
        return

    restaurants = await client.get_restaurants()
    print(f"Fetched restaurants: {len(restaurants)}")


def _print_log_summary(db_path: str) -> None:
    db = Path(db_path)
    if not db.exists():
        print(f"No DB found at {db_path}")
        return

    conn = sqlite3.connect(db)
    calls = conn.execute(
        "SELECT id, timestamp_utc, source, name, status, duration_ms FROM calls ORDER BY id DESC LIMIT 5"
    ).fetchall()

    print("\nRecent calls:")
    for row in calls:
        print(f"  call_id={row[0]} ts={row[1]} {row[2]} {row[3]} status={row[4]} dur_ms={row[5]}")

    recipes_table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recipes'"
    ).fetchone()
    if recipes_table_exists:
        recipe_count = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        ingredient_count = conn.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0]
        print(f"\nTyped recipe rows: recipes={recipe_count}, recipe_ingredients={ingredient_count}")

        latest_recipe_call = conn.execute(
            """
            SELECT id
            FROM calls
            WHERE source = 'http_get' AND name = '/recipes' AND status = 'ok'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        if latest_recipe_call:
            call_id = latest_recipe_call[0]
            print(f"\nLatest '/recipes' typed data (call_id={call_id}):")

            recipe_rows = conn.execute(
                """
                SELECT id, name, preparation_time_ms, prestige
                FROM recipes
                WHERE call_id = ?
                ORDER BY id
                LIMIT 5
                """,
                (call_id,),
            ).fetchall()

            for recipe_id, recipe_name, prep_ms, prestige in recipe_rows:
                ingredients = conn.execute(
                    """
                    SELECT ingredient_name, quantity
                    FROM recipe_ingredients
                    WHERE recipe_id = ?
                    ORDER BY ingredient_name
                    """,
                    (recipe_id,),
                ).fetchall()
                ing_text = ", ".join(f"{name} x{qty}" for name, qty in ingredients)
                print(f"  - {recipe_name} (prep={prep_ms}ms, prestige={prestige})")
                print(f"    ingredients: {ing_text}")

    conn.close()


async def _main() -> None:
    args = _parse_args()
    client = HackapizzaClient(
        team_id=args.team_id,
        api_key=args.api_key,
        base_url=args.base_url,
        enable_sql_logging=True,
        log_db_path=args.db_path,
    )

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, headers=client._headers) as session:
            client._session = session
            await _execute_call(client, args.call)
    finally:
        client._session = None
        client._close_sql_logging()

    _print_log_summary(args.db_path)


if __name__ == "__main__":
    asyncio.run(_main())
