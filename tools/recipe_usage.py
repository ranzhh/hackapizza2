"""
Best Underused Recipes — highest-prestige recipes that are least (or never) used.

Runs a single SQL query against the local Postgres mirror to return recipes
ordered by: fewest total menu appearances (ASC), then highest prestige (DESC).

Usage:
    uv run python -m tools.recipe_usage             # last 10 turns (default)
    uv run python -m tools.recipe_usage --turns 5    # last 5 turns
    uv run python -m tools.recipe_usage --top 20     # show top 20 (default: all)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DB_CONNSTR = os.getenv("HACKAPIZZA_SQL_CONNSTR") or os.getenv("DASHBOARD_DB_URL")
if not DB_CONNSTR:
    raise RuntimeError(
        "No database URL found. Set HACKAPIZZA_SQL_CONNSTR in your .env file "
        "or as an environment variable."
    )


# ───────────────────────────────────────────────────────────────────────
# Core query
# ───────────────────────────────────────────────────────────────────────


async def best_underused_recipes(
    num_turns: int = 10,
    top_n: int | None = None,
) -> list[dict]:
    """
    Return recipes sorted by least-used first, then highest prestige,
    each with usage count and ingredient list.

    Single SQL query — all filtering and aggregation in the database.
    """
    conn = await asyncpg.connect(DB_CONNSTR)
    try:
        # Ensure indexes (no-op if they exist)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calls_turn_id ON calls (turn_id);
            CREATE INDEX IF NOT EXISTS idx_restaurants_call_id ON restaurants (call_id);
            CREATE INDEX IF NOT EXISTS idx_menu_items_row_id ON restaurant_menu_items (restaurant_row_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe_id ON recipe_ingredients (recipe_id);
        """)

        rows = await conn.fetch("""
            WITH recent_turns AS (
                SELECT DISTINCT c.turn_id
                FROM calls c
                JOIN restaurants rest ON rest.call_id = c.id
                WHERE c.turn_id IS NOT NULL
                ORDER BY c.turn_id DESC
                LIMIT $1
            ),
            usage AS (
                SELECT mi.name AS dish, COUNT(*) AS total_uses
                FROM restaurant_menu_items mi
                JOIN restaurants rest ON rest.id = mi.restaurant_row_id
                JOIN calls c ON c.id = rest.call_id
                WHERE c.turn_id IN (SELECT turn_id FROM recent_turns)
                GROUP BY mi.name
            )
            SELECT r.name,
                   r.prestige,
                   r.preparation_time_ms,
                   COALESCE(u.total_uses, 0) AS total_uses,
                   COALESCE(
                       json_agg(json_build_object(
                           'ingredient_name', ri.ingredient_name,
                           'quantity', ri.quantity
                       )) FILTER (WHERE ri.ingredient_name IS NOT NULL),
                       '[]'
                   ) AS ingredients
            FROM recipes r
            LEFT JOIN usage u ON u.dish = r.name
            LEFT JOIN recipe_ingredients ri ON ri.recipe_id = r.id
            GROUP BY r.id, r.name, r.prestige, r.preparation_time_ms, u.total_uses
            ORDER BY COALESCE(u.total_uses, 0) ASC, r.prestige DESC
        """, num_turns)

        results = []
        for row in rows:
            ingredients = row["ingredients"]
            if isinstance(ingredients, str):
                ingredients = json.loads(ingredients)
            results.append({
                "name": row["name"],
                "prestige": row["prestige"],
                "preparation_time_ms": row["preparation_time_ms"],
                "total_uses": row["total_uses"],
                "ingredients": ingredients,
            })

        if top_n is not None:
            results = results[:top_n]

        return results
    finally:
        await conn.close()


# ───────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Highest-prestige recipes that are least used."
    )
    parser.add_argument(
        "--turns", type=int, default=10,
        help="Number of recent turns to analyse (default: 10)",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Only show the top N results (default: all)",
    )
    args = parser.parse_args()

    recipes = asyncio.run(best_underused_recipes(num_turns=args.turns, top_n=args.top))

    print(f"\n{'═'*70}")
    print(f"  BEST UNDERUSED RECIPES (last {args.turns} turns)")
    print(f"{'═'*70}")
    print(f"  {'Recipe':<35s} {'Prestige':>8s} {'Uses':>6s}")
    print(f"{'─'*70}")

    for r in recipes:
        tag = "★ UNUSED" if r["total_uses"] == 0 else ""
        print(f"  {r['name']:<35s} {r['prestige']:>8d} {r['total_uses']:>6d}  {tag}")

    unused_count = sum(1 for r in recipes if r["total_uses"] == 0)
    print(f"{'─'*70}")
    print(f"  {len(recipes)} recipes shown, {unused_count} never on any menu\n")


if __name__ == "__main__":
    main()
