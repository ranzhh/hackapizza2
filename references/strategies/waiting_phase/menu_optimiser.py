"""Waiting Phase — Archetype-Aware Menu Optimiser.

Given the *actual* post-auction inventory, selects which recipes to put on
the menu and at what (integer) price, maximising expected revenue while
factoring in:
  - customer archetype targeting
  - prestige scores
  - preparation time constraints
  - kitchen throughput limits

Data requirements (what the caller must supply):
  ┌────────────────────────────────┬─────────────────────────────────────────────┐
  │ Parameter                      │ Where it comes from                         │
  ├────────────────────────────────┼─────────────────────────────────────────────┤
  │ current_inventory              │ RestaurantSchema.inventory                  │
  │ recipes                        │ get_recipes() → List[RecipeSchema]          │
  │ ingredient_costs               │ Tracked locally (pre-bid vs post-bid Δ)     │
  │ archetype_weights              │ Config — which customer types to attract     │
  │ serving_phase_duration_ms      │ Estimated from SSE timing (~300_000)        │
  │ max_concurrent_dishes          │ Estimated / discovered from runtime         │
  │ competitor_restaurants         │ get_restaurants() (optional, for positioning)│
  └────────────────────────────────┴─────────────────────────────────────────────┘

Key facts from the API:
  • All recipe ingredient quantities are 1.
  • Menu prices must be int (>0) — the server rejects floats.
  • Ingredients expire at end of turn — unused stock = pure loss.
  • Customer archetype is influenced by price + prestige.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from hp2.core.schema.models import MenuItemSchema, RecipeSchema, RestaurantSchema


# ---------------------------------------------------------------------------
# Archetype configuration
# ---------------------------------------------------------------------------

ArchetypeName = Literal["explorer", "astrobaron", "sage", "family"]

# Price multipliers per archetype (applied to a base cost).
# These reflect the challenge description: explorers are cheap, astrobarons pay premium.
ARCHETYPE_PRICE_PROFILES: Dict[ArchetypeName, Dict[str, float]] = {
    "explorer": {
        "price_multiplier": 1.3,   # low margin, high volume
        "prestige_weight": 0.3,    # they don't care about prestige
        "speed_weight": 2.0,       # strongly prefer fast dishes
    },
    "astrobaron": {
        "price_multiplier": 3.0,   # premium pricing
        "prestige_weight": 1.5,    # want high-status dishes
        "speed_weight": 1.5,       # also want them fast
    },
    "sage": {
        "price_multiplier": 3.5,   # highest margins
        "prestige_weight": 2.0,    # prestige is everything
        "speed_weight": 0.3,       # very patient
    },
    "family": {
        "price_multiplier": 1.8,   # moderate
        "prestige_weight": 1.0,    # balanced
        "speed_weight": 0.5,       # patient
    },
}

DEFAULT_ARCHETYPE_WEIGHTS: Dict[ArchetypeName, float] = {
    "explorer": 0.10,
    "astrobaron": 0.35,
    "sage": 0.35,
    "family": 0.20,
}


# ---------------------------------------------------------------------------
# Supporting data structures
# ---------------------------------------------------------------------------

@dataclass
class MenuCandidate:
    """A recipe that *can* be cooked with current inventory."""

    recipe: RecipeSchema
    max_portions: int     # how many times we can make it
    price: int            # blended target price (integer)
    score: float          # multi-factor ranking score
    cost_floor: int       # minimum price to break even
    ingredients_used: Dict[str, int]  # ingredient → qty consumed for max_portions


@dataclass
class MenuPlan:
    """Output of the menu optimiser."""

    menu_items: List[MenuItemSchema]  # ready for save_menu
    total_expected_revenue: int
    total_portions: int
    inventory_utilisation: float  # fraction of inventory slots consumed
    nearly_feasible: List[Dict[str, Any]]  # recipes missing 1-2 ingredients (P2P targets)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Price computation
# ---------------------------------------------------------------------------

def compute_blended_price(
    recipe: RecipeSchema,
    ingredient_costs: Dict[str, int],
    archetype_weights: Dict[ArchetypeName, float],
    default_ingredient_cost: int = 5,
) -> Tuple[int, int]:
    """Compute a blended selling price targeting the given archetype mix.

    Returns (price, cost_floor) — both integers.
    """
    # Base cost: sum of what we paid for the ingredients
    cost_floor = sum(
        ingredient_costs.get(ing, default_ingredient_cost)
        for ing in recipe.ingredients
    )
    cost_floor = max(cost_floor, 1)

    # Weighted price across archetypes
    blended = 0.0
    total_weight = sum(archetype_weights.values())
    for archetype, weight in archetype_weights.items():
        profile = ARCHETYPE_PRICE_PROFILES.get(archetype)
        if not profile:
            continue
        # Price = cost × multiplier, boosted by prestige
        prestige_bonus = 1.0 + (recipe.prestige / 100.0) * profile["prestige_weight"]
        arch_price = cost_floor * profile["price_multiplier"] * prestige_bonus
        blended += (weight / total_weight) * arch_price

    price = max(cost_floor + 1, round(blended))  # never sell at or below cost
    return price, cost_floor


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_candidate(
    candidate: MenuCandidate,
    archetype_weights: Dict[ArchetypeName, float],
    serving_phase_duration_ms: int,
) -> float:
    """Multi-factor score combining revenue, prestige, speed, and saturation.

    Higher is better.
    """
    recipe = candidate.recipe

    # Revenue potential
    revenue_score = candidate.price * candidate.max_portions

    # Prestige contribution (weighted by archetype preferences)
    prestige_score = 0.0
    total_w = sum(archetype_weights.values())
    for arch, w in archetype_weights.items():
        profile = ARCHETYPE_PRICE_PROFILES.get(arch, {})
        prestige_score += (w / total_w) * recipe.prestige * profile.get("prestige_weight", 1.0)

    # Throughput: how many can we realistically cook in the serving phase?
    if recipe.preparation_time_ms > 0:
        max_cookable = serving_phase_duration_ms / recipe.preparation_time_ms
    else:
        max_cookable = float('inf')
    # Penalise if max_portions exceeds what we can actually cook
    throughput_ratio = min(1.0, max_cookable / max(candidate.max_portions, 1))

    # Speed factor (weighted by archetype speed preferences)
    speed_score = 0.0
    for arch, w in archetype_weights.items():
        profile = ARCHETYPE_PRICE_PROFILES.get(arch, {})
        # Faster dishes = higher speed score. Normalise by max observed ~5000ms.
        speed = max(0, 1.0 - recipe.preparation_time_ms / 10_000)
        speed_score += (w / total_w) * speed * profile.get("speed_weight", 1.0)

    # Combined score
    score = (
        revenue_score * 1.0
        + prestige_score * 10.0
        + speed_score * 50.0
    ) * throughput_ratio

    return score


# ---------------------------------------------------------------------------
# Near-miss detection (P2P market targets)
# ---------------------------------------------------------------------------

def find_nearly_feasible(
    inventory: Dict[str, Any],
    recipes: List[RecipeSchema],
    max_missing: int = 2,
) -> List[Dict[str, Any]]:
    """Find recipes that are 1–2 ingredients away from being cookable.

    These are prime candidates for P2P market purchases.
    """
    results = []
    for recipe in recipes:
        missing = []
        for ing in recipe.ingredients:
            if inventory.get(ing, 0) < 1:
                missing.append(ing)
        if 0 < len(missing) <= max_missing:
            results.append({
                "recipe_name": recipe.name,
                "prestige": recipe.prestige,
                "missing_ingredients": missing,
                "missing_count": len(missing),
            })
    # Sort by fewest missing, then highest prestige
    results.sort(key=lambda x: (x["missing_count"], -x["prestige"]))
    return results


# ---------------------------------------------------------------------------
# Core optimiser
# ---------------------------------------------------------------------------

def optimize_menu(
    current_inventory: Dict[str, Any],
    recipes: List[RecipeSchema],
    ingredient_costs: Optional[Dict[str, int]] = None,
    archetype_weights: Optional[Dict[ArchetypeName, float]] = None,
    serving_phase_duration_ms: int = 300_000,
    max_concurrent_dishes: int = 5,
    max_menu_items: int = 8,
    competitor_restaurants: Optional[List[RestaurantSchema]] = None,
) -> MenuPlan:
    """Optimise the menu for maximum expected revenue.

    Parameters
    ----------
    current_inventory : actual post-auction inventory {ingredient: qty}.
    recipes : all game recipes.
    ingredient_costs : what we paid per ingredient (for cost-floor pricing).
        If None, a default of 5 per ingredient is assumed.
    archetype_weights : targeting mix. Defaults to a balanced portfolio.
    serving_phase_duration_ms : estimated serving phase length.
    max_concurrent_dishes : kitchen parallelism limit.
    max_menu_items : maximum dishes on the menu.
    competitor_restaurants : optional — used for competitive positioning.

    Returns
    -------
    MenuPlan with menu items, revenue estimate, and P2P market suggestions.
    """
    ing_costs = ingredient_costs or {}
    arch_weights = archetype_weights or DEFAULT_ARCHETYPE_WEIGHTS

    # 1. Feasibility: find all recipes we can make at least once
    working_inv = {k: int(v) for k, v in current_inventory.items() if int(v) > 0}
    total_inventory_slots = sum(working_inv.values())

    candidates: List[MenuCandidate] = []
    for recipe in recipes:
        ingredients = list(recipe.ingredients.keys())
        if not ingredients:
            continue

        # All quantities are 1, so max portions = min(stock[ing] for ing in recipe)
        min_stock = min(working_inv.get(ing, 0) for ing in ingredients)
        if min_stock < 1:
            continue

        # Cap portions by what kitchen can cook in the phase
        if recipe.preparation_time_ms > 0:
            kitchen_cap = math.floor(
                serving_phase_duration_ms / recipe.preparation_time_ms
            ) * max_concurrent_dishes
        else:
            kitchen_cap = min_stock
        max_portions = min(min_stock, kitchen_cap)

        price, cost_floor = compute_blended_price(recipe, ing_costs, arch_weights)

        ing_used = {ing: max_portions for ing in ingredients}  # qty=1 per portion

        candidates.append(MenuCandidate(
            recipe=recipe,
            max_portions=max_portions,
            price=price,
            score=0.0,  # filled next
            cost_floor=cost_floor,
            ingredients_used=ing_used,
        ))

    # 2. Score candidates
    for c in candidates:
        c.score = score_candidate(c, arch_weights, serving_phase_duration_ms)

    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)

    # 3. Greedy selection: pick top candidates, deducting inventory
    selected: List[MenuCandidate] = []
    alloc_inv = dict(working_inv)
    total_portions = 0
    total_revenue = 0
    inv_consumed = 0

    for candidate in candidates:
        if len(selected) >= max_menu_items:
            break

        # Re-check feasibility against remaining allocated inventory
        recipe = candidate.recipe
        ingredients = list(recipe.ingredients.keys())
        avail_portions = min(alloc_inv.get(ing, 0) for ing in ingredients)
        if avail_portions < 1:
            continue

        # Recalc portions (may be less now due to shared ingredients)
        portions = min(avail_portions, candidate.max_portions)

        # Deduct from allocation
        for ing in ingredients:
            alloc_inv[ing] -= portions

        candidate.max_portions = portions
        candidate.ingredients_used = {ing: portions for ing in ingredients}
        selected.append(candidate)
        total_portions += portions
        total_revenue += candidate.price * portions
        inv_consumed += portions * len(ingredients)

    # 4. Build output
    menu_items = [
        MenuItemSchema(name=c.recipe.name, price=float(c.price))
        for c in selected
    ]

    inv_utilisation = inv_consumed / total_inventory_slots if total_inventory_slots > 0 else 0.0

    # 5. Find near-miss recipes for P2P market
    nearly_feasible = find_nearly_feasible(current_inventory, recipes)

    return MenuPlan(
        menu_items=menu_items,
        total_expected_revenue=total_revenue,
        total_portions=total_portions,
        inventory_utilisation=inv_utilisation,
        nearly_feasible=nearly_feasible,
        diagnostics={
            "candidates_evaluated": len(candidates),
            "selected_count": len(selected),
            "archetype_weights": arch_weights,
            "unallocated_inventory": {k: v for k, v in alloc_inv.items() if v > 0},
        },
    )
