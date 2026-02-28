"""Closed Bid Phase — Portfolio Bid Allocator.

Computes optimal blind-auction bids given:
  - current balance
  - available recipes with prestige scores
  - estimated clearing prices (from an EMA tracker or first-turn heuristic)
  - competitor intelligence (menus / inventories from get_restaurants)
  - target selling prices per recipe

Outputs a list of ``{"ingredient": str, "bid": int, "quantity": int}``
dicts ready to be passed to the ``closed_bid`` MCP tool.

Data requirements (what the caller must supply):
  ┌─────────────────────────────┬─────────────────────────────────────────────┐
  │ Parameter                   │ Where it comes from                         │
  ├─────────────────────────────┼─────────────────────────────────────────────┤
  │ balance                     │ RestaurantSchema.balance                    │
  │ recipes                     │ get_recipes() → List[RecipeSchema]          │
  │ recipe_target_prices        │ Menu optimiser / pricing oracle (int)       │
  │ estimated_clearing_prices   │ EMA over bid_history OR heuristic           │
  │ competitor_restaurants      │ get_restaurants() → List[RestaurantSchema]  │
  │ my_inventory                │ RestaurantSchema.inventory                  │
  └─────────────────────────────┴─────────────────────────────────────────────┘

Key facts discovered from the API:
  • All recipe ingredient quantities are 1.
  • bid and quantity in closed_bid must be int (>0).
  • Menu prices must also be int.
  • Competitor data (balance, inventory, menu, reputation) is fully visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from hp2.core.schema.models import RecipeSchema, RestaurantSchema


# ---------------------------------------------------------------------------
# Supporting data structures
# ---------------------------------------------------------------------------

@dataclass
class ScoredRecipe:
    """A recipe annotated with profitability and competition metrics."""

    recipe: RecipeSchema
    target_price: int  # expected selling price (int for the API)
    est_ingredient_cost: int  # sum of estimated clearing prices for ingredients
    profit_margin: int  # target_price - est_ingredient_cost
    efficiency: float  # profit_margin * prestige / n_ingredients
    competition_pressure: float  # 0..1 — how contested are its ingredients
    ingredient_overlap_score: float  # how many ingredients are shared with other selected recipes


@dataclass
class BidPlan:
    """The final output: a list of bids + diagnostic metadata."""

    bids: List[Dict[str, Any]]  # [{"ingredient": str, "bid": int, "quantity": int}]
    total_projected_spend: int
    selected_recipes: List[str]
    budget_remaining: int
    diagnostics: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Competitor intelligence
# ---------------------------------------------------------------------------

def estimate_ingredient_demand(
    recipes: List[RecipeSchema],
    competitors: List[RestaurantSchema],
    my_team_id: str = "",
) -> Dict[str, float]:
    """Return a 0…1 demand score per ingredient based on competitor menus.

    Higher score = more competitors want this ingredient = expect higher
    clearing prices.

    Logic:
    - For each competitor, look at their menu items.
    - Map menu item names → recipes → ingredients.
    - Count how many distinct competitors need each ingredient.
    - Normalise by total number of competitors.
    - Ingredients that competitors already have in inventory are discounted
      (they won't need to bid for them).
    """
    recipe_lookup: Dict[str, RecipeSchema] = {r.name: r for r in recipes}
    n_competitors = max(len(competitors) - 1, 1)  # exclude ourselves

    ingredient_bidder_count: Dict[str, int] = {}

    for rest in competitors:
        if rest.id == my_team_id:
            continue

        # What ingredients does this competitor likely need?
        needed: Set[str] = set()
        for menu_item in rest.menu.items:
            recipe = recipe_lookup.get(menu_item.name)
            if recipe:
                for ing in recipe.ingredients:
                    # Only count if they don't already have it in inventory
                    if rest.inventory.get(ing, 0) < 1:
                        needed.add(ing)

        for ing in needed:
            ingredient_bidder_count[ing] = ingredient_bidder_count.get(ing, 0) + 1

    # Normalise to 0..1
    return {
        ing: count / n_competitors
        for ing, count in ingredient_bidder_count.items()
    }


# ---------------------------------------------------------------------------
# Price estimation helpers
# ---------------------------------------------------------------------------

def ema_clearing_prices(
    price_history: Dict[str, List[int]],
    alpha: float = 0.3,
    default_price: int = 5,
) -> Dict[str, int]:
    """Compute an Exponential Moving Average clearing price per ingredient.

    Parameters
    ----------
    price_history : mapping of ingredient name → list of past clearing prices
        (most recent last).
    alpha : smoothing factor (higher = more weight on recent prices).
    default_price : returned when no history exists for an ingredient.

    Returns
    -------
    Dict[str, int] — estimated next clearing price per ingredient (int).
    """
    estimates: Dict[str, int] = {}
    for ing, prices in price_history.items():
        if not prices:
            estimates[ing] = default_price
            continue
        ema = float(prices[0])
        for p in prices[1:]:
            ema = alpha * p + (1 - alpha) * ema
        estimates[ing] = max(1, round(ema))
    return estimates


def heuristic_clearing_price(
    demand_score: float,
    base_price: int = 3,
    max_price: int = 15,
) -> int:
    """When no bid history is available, estimate price from demand score.

    Higher demand → higher expected clearing price.  Uses a simple linear
    interpolation between base_price and max_price.
    """
    return max(1, round(base_price + demand_score * (max_price - base_price)))


# ---------------------------------------------------------------------------
# Recipe scoring
# ---------------------------------------------------------------------------

def score_recipes(
    recipes: List[RecipeSchema],
    recipe_target_prices: Dict[str, int],
    estimated_clearing_prices: Dict[str, int],
    demand_scores: Dict[str, float],
    prestige_weight: float = 1.0,
    default_clearing_price: int = 5,
) -> List[ScoredRecipe]:
    """Score and rank recipes by risk-adjusted profitability.

    The efficiency metric balances:
    - Profit margin: how much we earn after ingredient cost
    - Prestige: influences customer attraction & reputation
    - Ingredient count: fewer ingredients = easier to fully acquire
    - Competition pressure: how contested the ingredients are
    """
    scored: List[ScoredRecipe] = []

    for recipe in recipes:
        target_price = recipe_target_prices.get(recipe.name)
        if target_price is None or target_price <= 0:
            continue  # skip recipes we don't plan to sell

        ingredients = list(recipe.ingredients.keys())
        n_ing = len(ingredients)
        if n_ing == 0:
            continue

        # Estimated total ingredient cost
        est_cost = sum(
            estimated_clearing_prices.get(ing, default_clearing_price)
            for ing in ingredients
        )

        profit = target_price - est_cost
        if profit <= 0:
            continue  # unprofitable at current prices

        # Average competition pressure across ingredients
        avg_competition = sum(demand_scores.get(ing, 0.0) for ing in ingredients) / n_ing

        # Efficiency: profit × prestige / (n_ingredients × competition penalty)
        competition_penalty = 1.0 + avg_competition  # 1.0 .. 2.0
        efficiency = (profit * (recipe.prestige ** prestige_weight)) / (n_ing * competition_penalty)

        scored.append(ScoredRecipe(
            recipe=recipe,
            target_price=target_price,
            est_ingredient_cost=est_cost,
            profit_margin=profit,
            efficiency=efficiency,
            competition_pressure=avg_competition,
            ingredient_overlap_score=0.0,  # filled in later
        ))

    # Sort: highest efficiency first
    scored.sort(key=lambda s: s.efficiency, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Ingredient overlap bonus
# ---------------------------------------------------------------------------

def compute_overlap_scores(scored: List[ScoredRecipe]) -> None:
    """Mutate scored recipes in-place: add overlap bonus for shared ingredients.

    Recipes that share ingredients with higher-ranked recipes are more
    resilient to partial auction fills (winning one ingredient helps
    multiple dishes).
    """
    if len(scored) < 2:
        return

    # Collect all ingredients from the top-N candidates
    all_ings: Dict[str, int] = {}  # ingredient → count of recipes needing it
    for s in scored:
        for ing in s.recipe.ingredients:
            all_ings[ing] = all_ings.get(ing, 0) + 1

    for s in scored:
        shared = sum(1 for ing in s.recipe.ingredients if all_ings.get(ing, 0) > 1)
        s.ingredient_overlap_score = shared / max(len(s.recipe.ingredients), 1)


# ---------------------------------------------------------------------------
# Core allocation function
# ---------------------------------------------------------------------------

def allocate_bids(
    balance: float,
    recipes: List[RecipeSchema],
    recipe_target_prices: Dict[str, int],
    estimated_clearing_prices: Dict[str, int],
    competitor_restaurants: List[RestaurantSchema],
    my_team_id: str = "",
    my_inventory: Optional[Dict[str, Any]] = None,
    budget_fraction: float = 0.70,
    markup_pct: float = 0.10,
    max_recipes: int = 5,
    portions_per_recipe: int = 2,
    prestige_weight: float = 0.5,
) -> BidPlan:
    """Compute optimal blind-auction bids.

    Parameters
    ----------
    balance : current cash.
    recipes : all available recipes (from get_recipes).
    recipe_target_prices : mapping recipe name → intended selling price (int).
    estimated_clearing_prices : mapping ingredient → EMA or heuristic price (int).
        If empty, heuristic prices are generated from competitor demand.
    competitor_restaurants : all restaurants from get_restaurants (for demand estimation).
    my_team_id : our restaurant id (to exclude ourselves from competitor analysis).
    my_inventory : ingredients we already own (Dict[str, int|float]).
        These are subtracted from what we need to bid for.
    budget_fraction : max fraction of balance to spend on bids (0..1).
    markup_pct : how far above the estimated clearing price to bid (0..1).
    max_recipes : max number of distinct recipe sets to bid for.
    portions_per_recipe : how many portions of each recipe to target.
    prestige_weight : exponent applied to prestige in the scoring function.

    Returns
    -------
    BidPlan with bids list, projected spend, selected recipes, and diagnostics.
    """
    my_inv = my_inventory or {}
    spending_cap = int(balance * budget_fraction)

    # 1. Estimate ingredient demand from competitor intelligence
    demand_scores = estimate_ingredient_demand(recipes, competitor_restaurants, my_team_id)

    # 2. Fill in missing clearing-price estimates using heuristic
    all_ingredients: Set[str] = set()
    for r in recipes:
        all_ingredients.update(r.ingredients.keys())

    full_clearing_prices = dict(estimated_clearing_prices)
    for ing in all_ingredients:
        if ing not in full_clearing_prices:
            full_clearing_prices[ing] = heuristic_clearing_price(demand_scores.get(ing, 0.0))

    # 3. Score and rank recipes
    scored = score_recipes(
        recipes,
        recipe_target_prices,
        full_clearing_prices,
        demand_scores,
        prestige_weight=prestige_weight,
    )
    compute_overlap_scores(scored)

    # Re-sort incorporating overlap bonus (10% weight)
    scored.sort(key=lambda s: s.efficiency * (1.0 + 0.1 * s.ingredient_overlap_score), reverse=True)

    # 4. Select top recipes and build the bid cart
    selected = scored[:max_recipes]
    bids_cart: Dict[str, Dict[str, int]] = {}  # ingredient → {"quantity": N, "bid": P}
    projected_spend = 0

    for sr in selected:
        for ing in sr.recipe.ingredients:
            already_owned = int(my_inv.get(ing, 0))
            needed = max(0, portions_per_recipe - already_owned)
            if needed <= 0:
                continue

            est_price = full_clearing_prices.get(ing, 5)
            bid_price = max(1, round(est_price * (1.0 + markup_pct)))

            cost = bid_price * needed
            if projected_spend + cost > spending_cap:
                # Try to fit partial
                affordable = (spending_cap - projected_spend) // bid_price
                if affordable <= 0:
                    continue
                needed = affordable
                cost = bid_price * needed

            if ing in bids_cart:
                bids_cart[ing]["quantity"] += needed
                # Keep the higher bid price
                bids_cart[ing]["bid"] = max(bids_cart[ing]["bid"], bid_price)
            else:
                bids_cart[ing] = {"quantity": needed, "bid": bid_price}

            projected_spend += cost

    # 5. Final budget check — trim if we went over
    if projected_spend > spending_cap:
        # Simple proportional scaling
        ratio = spending_cap / projected_spend
        projected_spend = 0
        for ing, entry in bids_cart.items():
            entry["quantity"] = max(1, round(entry["quantity"] * ratio))
            projected_spend += entry["bid"] * entry["quantity"]

    # 6. Format for the closed_bid MCP tool
    bids_list = [
        {"ingredient": ing, "bid": data["bid"], "quantity": data["quantity"]}
        for ing, data in bids_cart.items()
        if data["quantity"] > 0
    ]

    return BidPlan(
        bids=bids_list,
        total_projected_spend=projected_spend,
        selected_recipes=[sr.recipe.name for sr in selected],
        budget_remaining=int(balance) - projected_spend,
        diagnostics={
            "demand_scores": demand_scores,
            "scored_recipes_count": len(scored),
            "clearing_prices_used": full_clearing_prices,
        },
    )
