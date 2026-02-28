# Serving Phase — Kitchen Load & Order Management

## Phase Overview

During the **Serving** phase, customers arrive via SSE `client_spawned`
events with natural-language order requests (potentially including
intolerance hints).  The restaurant must:

1. Match requests to menu dishes
2. Check for intolerance conflicts
3. Start cooking (`prepare_dish`) — this is async, fires `preparation_complete`
4. Serve when ready (`serve_dish`)
5. Close the restaurant if overwhelmed to protect reputation

## What Data Is Available

| Source | Fields | Notes |
|---|---|---|
| `get_my_restaurant` | `inventory`, `kitchen: List[Dict]`, `is_open`, `reputation`, `menu` | Kitchen list = currently cooking dishes |
| `get_recipes` | `name`, `ingredients: Dict[str, 1]`, `preparation_time_ms`, `prestige` | Prep time is key |
| SSE `client_spawned` | `clientName`, `orderText` | Natural language — may contain intolerance keywords |
| SSE `preparation_complete` | dish name | Fires when a dish finishes cooking |
| Phase timing | **Not directly available** | Must be estimated from SSE events |
| Customer intolerances | Embedded in `orderText` | Must be parsed by the LLM or keyword matching |

## What Must Be Built / Tracked Locally

1. **Phase start timestamp**: Record `time.monotonic()` when the serving
   phase begins (from `game_phase_changed` SSE event).  Combined with an
   estimated phase duration (~300s), this gives `phase_time_remaining_ms`.

2. **Kitchen queue model**: The `kitchen` field from `RestaurantSchema` is
   `List[Dict[str, Any]]` — the exact structure is TBD (needs runtime
   discovery).  Expected keys might include `recipe_name` / `dish_name`,
   `started_at`, `status`.  The load manager works with whatever is there.

3. **Ingredient consumption tracker**: As dishes are prepared, deduct
   ingredients from a local inventory shadow to avoid over-promising.

4. **Customer queue**: Track pending customers, their orders, and which
   have been served.  The agent currently does this via `GameState.pending_customers`.

## Strategy: Multi-Signal Circuit Breaker

The load manager goes beyond a simple "close if queue is full":

### Signal 1: Capacity Saturation
Close if `len(kitchen_queue) >= max_concurrent_dishes`.

### Signal 2: Time Boundary (Little's Law)
Estimate total remaining processing time.  If it exceeds the phase time
remaining (minus a safety buffer), close — any new order would be
unfulfillable.

### Signal 3: Ingredient Exhaustion
If the local inventory shadow shows we can't cook any of our menu dishes,
close immediately.  Staying open with nothing to serve destroys reputation.

### Signal 4: Reputation Guard
If reputation is already low, be more aggressive about closing.  The cost
of a failed order at low reputation is proportionally worse.

### Signal 5: Revenue vs Risk
Before accepting an order, compare `expected_revenue` against
`expected_reputation_loss × reputation_value`.  If the risk-adjusted
return is negative, reject (close temporarily).

## The Customer Serving Pipeline

```
client_spawned → match_order_to_dish → check_intolerances → check_inventory
  → prepare_dish → [wait for preparation_complete] → serve_dish
```

The `prioritise_customers` function ranks pending customers by expected
value (price of their likely dish × urgency), helping the agent decide
which orders to fill first when capacity is limited.

## API Contracts

```text
prepare_dish(dish_name=str)           # starts async cooking
serve_dish(dish_name=str, client_id=str)  # serves a finished dish
update_restaurant_is_open(is_open=bool)   # circuit breaker
```

## Key Risk Mitigations

- **Intolerance first**: Always check for intolerance keywords before
  preparing.  A violation = federal sanctions + $0 revenue.
- **Shadow inventory**: Never trust the server inventory mid-phase — track
  locally as dishes are queued for preparation.
- **Graceful degradation**: When closing, dump surplus ingredients on the
  P2P market as a last-ditch revenue recovery.
- **Re-open logic**: After the kitchen queue drains, if time and ingredients
  remain, re-open the restaurant.
