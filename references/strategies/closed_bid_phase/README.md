# Closed Bid Phase — Competitive Procurement Strategy

## Phase Overview

During the **Closed Bid** phase every restaurant submits blind-auction bids
(`ingredient`, `bid`, `quantity` — all **integers**).  The server resolves
them highest-bid-first; you may receive 0…N of what you requested.  Results
(including competitor prices) are revealed only *after* the phase ends.

## What Data Is Available

| Source | Fields | Notes |
|---|---|---|
| `get_my_restaurant` | `balance`, `inventory`, `reputation`, `kitchen`, `menu`, `is_open` | Live snapshot |
| `get_restaurants` | Same fields for **every** competitor | Full intel on their balance/inventory/menu |
| `get_recipes` | `name`, `ingredients: Dict[str, 1]`, `preparation_time_ms`, `prestige` | All quantities are 1 |
| `get_market_entries` | TBD (schema is empty) | May contain P2P offers from other teams |
| `get_bid_history(turn_id)` | TBD (schema is empty) | Reveals past auction clearing prices — **critical** for price estimation |
| Competitor menus | `restaurants[i].menu.items` | Shows what others plan to cook → predicts ingredient demand |
| Competitor inventories | `restaurants[i].inventory` | Shows what they already own → ingredients they won't bid on |

## What Must Be Built / Tracked Locally

1. **Clearing-price history** (`Dict[str, List[int]]`): After each turn's
   bid results are revealed, record the winning price per ingredient.  The
   `allocate_bids` function uses an EMA (Exponential Moving Average) over
   this history to predict the next clearing price.

2. **Ingredient demand estimator**: By inspecting competitor menus and
   inventories we can estimate which ingredients will be contested (high
   demand → bid higher) vs. unchallenged (low demand → bid the floor).

3. **Revenue model**: Maps each recipe to an expected selling price
   (integer).  This is the `recipe_target_prices` input and should come
   from the menu optimiser or a pricing oracle.

## Strategy: Portfolio Bid Allocation

The algorithm goes beyond naïve "buy ingredients for one recipe":

1. **Score recipes** by `efficiency = target_price * prestige_weight / (n_ingredients * est_total_cost)`.
   This rewards dishes that are profitable *and* prestigious *and* cheap to acquire.
2. **Estimate competition** per ingredient by counting how many competitors
   have that ingredient's recipes on their menu or lack it in inventory.
3. **Cluster by ingredient overlap** — prefer recipes that share ingredients
   so a single bid covers multiple dishes, improving partial-fill resilience.
4. **Allocate budget** with a safety margin: spend more on high-EV
   ingredients, keep reserves for the P2P market.
5. **Output integer bids** ready for the `closed_bid` MCP tool.

## API Contract

```text
closed_bid(bids=[{"ingredient": str, "bid": int (>0), "quantity": int (>0)}, ...])
```

Prices **must** be integers.  Quantities **must** be integers.

## Key Risk Mitigations

- **Safety margin**: Never spend more than `budget_fraction` (default 0.70) of balance.
- **Over-bid buffer**: Bid `markup_pct` above estimated clearing price to
  win ties, but cap so total spend stays within budget.
- **Diversification**: Spread bids across multiple recipe sets so that
  partial fills still yield at least some cookable dishes.
- **Market fallback**: Reserve budget for P2P market purchases in the
  Waiting phase for any ingredients missed.
- **Competitor-aware bidding**: Ingredients that appear on many competitor
  menus get a higher estimated clearing price, reflecting real demand.
