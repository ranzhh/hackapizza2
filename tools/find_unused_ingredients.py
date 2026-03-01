import asyncpg
import os
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from typing import Any
import re
from dotenv import load_dotenv
from typing import TypedDict

class MatrixReturn(TypedDict):
    turn_id: int 
    restaurants: list[str]
    ingredients: list[str]
    bids: dict[str, dict[str, dict | None]]
    error: str | None

load_dotenv()

DB_CONNSTR = (
    os.getenv("HACKAPIZZA_SQL_CONNSTR")
    or os.getenv("DASHBOARD_DB_URL")
)
if not DB_CONNSTR:
    raise RuntimeError(
        "No database URL found. Set HACKAPIZZA_SQL_CONNSTR in your .env file "
        "or as an environment variable."
    )


pool: asyncpg.Pool | None = None
_BID_RE = re.compile(
    r"Restaurant\s+(\S+)\s+try to buy:(\d+)\s+(.+?)\s+at single price of:\s*(\d+)"
    r"\s+result:Bought\s+\d+\s+.+?\s+for\s+\d+"
)

async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DB_CONNSTR, min_size=2, max_size=10)
    return pool


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
                "restaurant": int(restaurant),
                "ingredient": ingredient.strip(),
                "quantity": int(qty),
                "unit_price": int(price),
            }
        )
    return results

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

async def get_bids(turn_id: str | None = Query(None)) -> MatrixReturn:
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
    empty: MatrixReturn = {"turn_id": turn_id, "restaurants": [], "ingredients": [], "bids": {}, "error": None}

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
        return MatrixReturn(
            turn_id=actual_turn_id,
            restaurants=[],
            ingredients=[],
            bids={},
            error="Messages found but no bids could be parsed (check message format)",
        )

    # ------------------------------------------------------------------ #
    # Build matrix                                                        #
    # ------------------------------------------------------------------ #
    restaurants: set[int] = set()
    ingredients: set[str] = set()
    for bid in all_bids:
        restaurants.add(bid["restaurant"])
        ingredients.add(bid["ingredient"])

    sorted_restaurants = sorted(restaurants)
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

    return MatrixReturn(
        turn_id=actual_turn_id,
        restaurants=sorted_restaurants,
        ingredients=sorted_ingredients,
        bids=matrix,
        error=None,
    )

def get_team_bids(bid_matrix, team_id: int):
    team_bids = []
    for ingredient, rest_bids in bid_matrix.items():
        for restaurant, bid in rest_bids.items():
            if bid and team_id == restaurant: 
                team_bids.append({
                    "ingredient": ingredient,
                    "quantity": bid["quantity"],
                    "unit_price": bid["unit_price"],
                })
    return team_bids
    
def get_avg_bid_item(bid_matrix, ingredient: str):
    total_qty = 0
    total_price = 0
    for _, bid in bid_matrix.get(ingredient, {}).items():
        if bid:
            total_qty += bid["quantity"]
            total_price += bid["quantity"] * bid["unit_price"]
    
    return total_price / total_qty if total_qty > 0 else None
           
def get_total_qty_item(bid_matrix, ingredient: str):
    total_qty = 0
    for _, bid in bid_matrix.get(ingredient, {}).items():
        if bid:
            total_qty += bid["quantity"]
    
    return total_qty

def get_K_bidded(bid_matrix, K: int, top = False):
    item_avgs = []
    for ingredient in bid_matrix.keys():
        avg_price = get_avg_bid_item(bid_matrix, ingredient)
        if avg_price is not None:
            item_avgs.append((ingredient, avg_price))
    
    sorted_items = sorted(item_avgs, key=lambda x: x[1], reverse=top)
    return sorted_items[:K]

def main():
    import asyncio

    turn_id = "1"
    result = asyncio.run(get_bids(turn_id))

    if result["error"]:
        print(f"Error: {result['error']}")
        return

    bid_matrix = result["bids"]
    restaurants = result["restaurants"]
    ingredients = result["ingredients"]

    # ── 1. Full bid matrix ────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  BID MATRIX — Turn {result['turn_id']}")
    print(f"  {len(restaurants)} restaurant(s), {len(ingredients)} ingredient(s)")
    print(f"{'='*60}")
    for ing, rest_bids in bid_matrix.items():
        print(f"\n  {ing}:")
        for rest, bid in rest_bids.items():
            if bid:
                print(f"    Restaurant {rest}: {bid['quantity']} @ {bid['unit_price']} each")
            else:
                print(f"    Restaurant {rest}: No bid")

    # ── 2. Per-team breakdown ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  PER-TEAM BIDS")
    print(f"{'='*60}")
    for rest in restaurants:
        team_bids = get_team_bids(bid_matrix, rest)
        total_spend = sum(b["quantity"] * b["unit_price"] for b in team_bids)
        print(f"\n  Restaurant {rest} ({len(team_bids)} bid(s), total spend: {total_spend}):")
        for b in team_bids:
            print(f"    {b['ingredient']}: {b['quantity']} @ {b['unit_price']}")

    # ── 3. Average bid price per ingredient ───────────────────────────
    print(f"\n{'='*60}")
    print("  AVG BID PRICE PER INGREDIENT")
    print(f"{'='*60}")
    for ing in ingredients:
        avg = get_avg_bid_item(bid_matrix, ing)
        qty = get_total_qty_item(bid_matrix, ing)
        if avg is not None:
            print(f"  {ing:<40s}  avg={avg:>8.1f}  total_qty={qty}")
        else:
            print(f"  {ing:<40s}  (no bids)")

    # ── 4. Top 5 most expensive ingredients ───────────────────────────
    print(f"\n{'='*60}")
    print("  TOP 5 MOST EXPENSIVE (by avg bid price)")
    print(f"{'='*60}")
    for rank, (ing, avg) in enumerate(get_K_bidded(bid_matrix, K=5, top=True), 1):
        print(f"  {rank}. {ing:<40s}  avg={avg:.1f}")

    # ── 5. Top 5 cheapest ingredients ─────────────────────────────────
    print(f"\n{'='*60}")
    print("  TOP 5 CHEAPEST (by avg bid price)")
    print(f"{'='*60}")
    for rank, (ing, avg) in enumerate(get_K_bidded(bid_matrix, K=5, top=False), 1):
        print(f"  {rank}. {ing:<40s}  avg={avg:.1f}")

    # ── 6. Ingredients nobody bid on ──────────────────────────────────
    unbid = [ing for ing in ingredients if get_avg_bid_item(bid_matrix, ing) is None]
    if unbid:
        print(f"\n{'='*60}")
        print(f"  INGREDIENTS WITH NO BIDS ({len(unbid)})")
        print(f"{'='*60}")
        for ing in unbid:
            print(f"  - {ing}")

if __name__ == "__main__":
    main()