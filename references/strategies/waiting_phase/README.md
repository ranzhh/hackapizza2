# Waiting Phase — Menu Optimisation (Archetype-Aware Knapsack)

## Phase Overview

After the blind auction resolves, you know **exactly** what you won.
Ingredients expire at turn end, so any unused stock is pure loss.  This is
the last chance to set the menu and adjust prices before customers arrive.

The goal: **choose which recipes to offer and at what price**, maximising
expected revenue while consuming as much inventory as possible.

## What Data Is Available

| Source | Fields | Notes |
|---|---|---|
| `get_my_restaurant` | `balance`, `inventory: Dict[str, int\|float]`, `reputation` | Inventory now reflects auction results |
| `get_recipes` | `name`, `ingredients: Dict[str, 1]`, `preparation_time_ms`, `prestige` | All ingredient quantities are 1 |
| `get_restaurants` | Competitor menus, inventories, reputations | Competitive positioning intel |
| `get_market_entries` | TBD | P2P buy/sell offers — useful for filling gaps |
| Customer archetypes (from challenge spec) | See table below | Determines who your prices attract |

## Customer Archetypes & Pricing

Pricing is the **primary lever** for selecting which customer archetype
visits your restaurant.  From the challenge spec:

| Archetype | Time Sensitivity | Budget | Quality Prio | Ideal Price Range | Ideal Prestige |
|---|---|---|---|---|---|
| **Galactic Explorer** | High (fast) | Low | Low | Cheap (floor) | Low |
| **Astrobaron** | Very High | High | High | Expensive | High |
| **Cosmic Sage** | Low (patient) | High | Very High | Expensive | Very High |
| **Orbital Family** | Low (patient) | Medium | Medium | Moderate | Medium |

The menu optimiser lets you pick a **target archetype mix** and
derives price ranges accordingly.

## What Must Be Built / Tracked Locally

1. **Ingredient cost ledger**: How much we actually paid per ingredient in
   the auction.  This sets the floor for dish pricing — selling below cost
   is a loss.  Must be computed after bid results by comparing pre-bid
   balance with post-bid balance and dividing by quantities won.

2. **Archetype targeting config**: A dict specifying which archetypes to
   prioritise and in what proportion (e.g., `{"astrobaron": 0.5, "sage": 0.3,
   "family": 0.2}`).

3. **Phase time estimate**: Needed to calculate how many dishes can actually
   be cooked in the serving phase (prep time constraint).

## Strategy: Archetype-Weighted Greedy Knapsack

The algorithm improves on a plain greedy approach:

1. **Feasibility filter**: Only consider recipes where the inventory fully
   covers all ingredients (all quantities are 1, so just check presence).
2. **Price derivation**: For each archetype, compute the ideal price for a
   dish based on its prestige and ingredient count.  Blend prices according
   to the archetype target mix.
3. **Value scoring**: `score = blended_price × prestige_weight × throughput_factor`.
   The throughput factor penalises slow recipes when the serving phase is short.
4. **Greedy selection**: Pick highest-score recipes first, deducting
   ingredients from a working inventory copy.  Track how many portions of
   each dish are supported.
5. **Portion capping**: Limit portions per dish to what the kitchen can
   actually cook within the serving phase.
6. **Output integer prices** for `save_menu`.

## API Contract

```text
save_menu(items=[{"name": str, "price": int (>0)}, ...])
```

Prices **must** be integers.

## P2P Market Integration

After selecting the menu, if a recipe is *almost* feasible (missing 1–2
ingredients), the optimiser flags it so the agent can attempt a P2P market
purchase.  This maximises the value of partially-won ingredient sets.
