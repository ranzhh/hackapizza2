"""Serving Phase — Kitchen Load Manager & Customer Prioritiser.

Decides:
  1. Whether to close/re-open the restaurant (circuit breaker).
  2. Which pending customers to serve first (prioritisation).
  3. Whether we have enough ingredients to accept a new order (shadow inventory).

Data requirements (what the caller must supply):
  ┌──────────────────────────────────┬──────────────────────────────────────────────┐
  │ Parameter                        │ Where it comes from                          │
  ├──────────────────────────────────┼──────────────────────────────────────────────┤
  │ active_kitchen_queue             │ RestaurantSchema.kitchen (List[Dict])        │
  │ recipes                          │ get_recipes() → List[RecipeSchema]           │
  │ phase_time_remaining_ms          │ Tracked locally (phase_start + est. duration)│
  │ shadow_inventory                 │ Maintained locally by deducting on prepare   │
  │ menu_items                       │ RestaurantSchema.menu.items                  │
  │ reputation                       │ RestaurantSchema.reputation                  │
  │ pending_customers                │ GameState.pending_customers                  │
  │ menu_prices                      │ Dict[str, int] from current menu             │
  │ max_concurrent_dishes            │ Config / discovered at runtime (default 5)   │
  └──────────────────────────────────┴──────────────────────────────────────────────┘

Key facts from the API:
  • kitchen is List[Dict[str, Any]] — exact keys TBD (likely includes
    dish/recipe name and status).  We handle this flexibly.
  • prepare_dish is async: fires SSE preparation_complete when done.
  • All recipe ingredient quantities are 1.
  • Customer intolerances are embedded in natural-language orderText.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from hp2.core.schema.models import RecipeSchema


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------

class LoadSignal(str, Enum):
    """Reason for the circuit-breaker decision."""
    OK = "ok"
    CAPACITY_FULL = "capacity_full"
    TIME_EXCEEDED = "time_exceeded"
    NO_INGREDIENTS = "no_ingredients"
    LOW_REPUTATION = "low_reputation"
    NEGATIVE_EV = "negative_expected_value"


@dataclass
class CircuitBreakerResult:
    """Output of the load manager decision."""
    should_close: bool
    signal: LoadSignal
    details: str
    kitchen_utilisation: float  # 0..1
    estimated_queue_drain_ms: int
    can_cook_any_dish: bool


@dataclass
class CustomerPriority:
    """A pending customer annotated with priority score."""
    client_name: str
    order_text: str
    matched_dish: Optional[str]  # best-guess menu match (set by caller/LLM)
    expected_revenue: int
    priority_score: float  # higher = serve first
    has_intolerance_risk: bool


# ---------------------------------------------------------------------------
# Kitchen introspection helpers
# ---------------------------------------------------------------------------

def extract_dish_names_from_kitchen(
    kitchen_queue: List[Dict[str, Any]],
) -> List[str]:
    """Extract dish/recipe names from the kitchen queue dicts.

    The exact key is TBD — we try several plausible field names.
    """
    names = []
    candidate_keys = ["recipe_name", "dish_name", "recipeName", "dishName", "name", "dish"]
    for order in kitchen_queue:
        for key in candidate_keys:
            val = order.get(key)
            if val and isinstance(val, str):
                names.append(val)
                break
    return names


def compute_queue_drain_time(
    kitchen_queue: List[Dict[str, Any]],
    recipes: List[RecipeSchema],
) -> int:
    """Estimate total ms to drain the current kitchen queue.

    Assumes sequential processing (worst case).  If the kitchen processes
    in parallel, divide by max_concurrent_dishes.
    """
    prep_times: Dict[str, int] = {r.name: r.preparation_time_ms for r in recipes}
    dish_names = extract_dish_names_from_kitchen(kitchen_queue)
    return sum(prep_times.get(name, 0) for name in dish_names)


# ---------------------------------------------------------------------------
# Shadow inventory
# ---------------------------------------------------------------------------

def can_cook_dish(
    dish_name: str,
    shadow_inventory: Dict[str, int],
    recipes: List[RecipeSchema],
) -> bool:
    """Check whether we have all ingredients for one portion of a dish."""
    recipe_map = {r.name: r for r in recipes}
    recipe = recipe_map.get(dish_name)
    if not recipe:
        return False
    for ing in recipe.ingredients:
        if shadow_inventory.get(ing, 0) < 1:
            return False
    return True


def deduct_ingredients(
    dish_name: str,
    shadow_inventory: Dict[str, int],
    recipes: List[RecipeSchema],
) -> bool:
    """Deduct ingredients for one portion.  Returns False if insufficient."""
    recipe_map = {r.name: r for r in recipes}
    recipe = recipe_map.get(dish_name)
    if not recipe:
        return False
    # Verify first
    for ing in recipe.ingredients:
        if shadow_inventory.get(ing, 0) < 1:
            return False
    # Deduct
    for ing in recipe.ingredients:
        shadow_inventory[ing] -= 1
    return True


def any_dish_cookable(
    menu_dish_names: List[str],
    shadow_inventory: Dict[str, int],
    recipes: List[RecipeSchema],
) -> bool:
    """Check if *any* menu dish can still be cooked with remaining inventory."""
    return any(
        can_cook_dish(name, shadow_inventory, recipes)
        for name in menu_dish_names
    )


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def should_close_restaurant(
    active_kitchen_queue: List[Dict[str, Any]],
    recipes: List[RecipeSchema],
    phase_time_remaining_ms: int,
    shadow_inventory: Dict[str, int],
    menu_dish_names: List[str],
    reputation: float = 100.0,
    max_concurrent_dishes: int = 5,
    safety_buffer_ms: int = 8_000,
    low_reputation_threshold: float = 40.0,
) -> CircuitBreakerResult:
    """Multi-signal circuit breaker for the serving phase.

    Parameters
    ----------
    active_kitchen_queue : current kitchen state from RestaurantSchema.kitchen.
    recipes : all game recipes (for prep time lookups).
    phase_time_remaining_ms : estimated ms left in the serving phase.
    shadow_inventory : locally-tracked inventory (deducted as dishes are queued).
    menu_dish_names : names of dishes currently on the menu.
    reputation : current restaurant reputation (0..100+).
    max_concurrent_dishes : kitchen parallelism limit.
    safety_buffer_ms : extra ms to reserve for network/processing latency.
    low_reputation_threshold : below this, be more aggressive about closing.

    Returns
    -------
    CircuitBreakerResult with the decision, signal, and diagnostics.
    """
    queue_len = len(active_kitchen_queue)
    utilisation = queue_len / max(max_concurrent_dishes, 1)

    # Estimate drain time (worst case: sequential)
    total_drain_ms = compute_queue_drain_time(active_kitchen_queue, recipes)
    # Adjust for parallelism
    parallel_drain_ms = total_drain_ms // max(max_concurrent_dishes, 1)

    cookable = any_dish_cookable(menu_dish_names, shadow_inventory, recipes)

    # --- Signal 1: Capacity saturation ---
    if queue_len >= max_concurrent_dishes:
        return CircuitBreakerResult(
            should_close=True,
            signal=LoadSignal.CAPACITY_FULL,
            details=f"Kitchen at capacity: {queue_len}/{max_concurrent_dishes} slots used.",
            kitchen_utilisation=utilisation,
            estimated_queue_drain_ms=parallel_drain_ms,
            can_cook_any_dish=cookable,
        )

    # --- Signal 2: Time boundary ---
    if (parallel_drain_ms + safety_buffer_ms) >= phase_time_remaining_ms:
        return CircuitBreakerResult(
            should_close=True,
            signal=LoadSignal.TIME_EXCEEDED,
            details=(
                f"Queue drain ({parallel_drain_ms}ms) + buffer ({safety_buffer_ms}ms) "
                f">= remaining phase time ({phase_time_remaining_ms}ms)."
            ),
            kitchen_utilisation=utilisation,
            estimated_queue_drain_ms=parallel_drain_ms,
            can_cook_any_dish=cookable,
        )

    # --- Signal 3: Ingredient exhaustion ---
    if not cookable:
        return CircuitBreakerResult(
            should_close=True,
            signal=LoadSignal.NO_INGREDIENTS,
            details="Cannot cook any menu dish with remaining inventory.",
            kitchen_utilisation=utilisation,
            estimated_queue_drain_ms=parallel_drain_ms,
            can_cook_any_dish=False,
        )

    # --- Signal 4: Low reputation guard ---
    if reputation < low_reputation_threshold and queue_len >= max_concurrent_dishes - 1:
        return CircuitBreakerResult(
            should_close=True,
            signal=LoadSignal.LOW_REPUTATION,
            details=(
                f"Reputation ({reputation:.1f}) below threshold ({low_reputation_threshold}) "
                f"and kitchen nearly full ({queue_len}/{max_concurrent_dishes})."
            ),
            kitchen_utilisation=utilisation,
            estimated_queue_drain_ms=parallel_drain_ms,
            can_cook_any_dish=cookable,
        )

    # --- All clear ---
    return CircuitBreakerResult(
        should_close=False,
        signal=LoadSignal.OK,
        details="Kitchen operational — accepting orders.",
        kitchen_utilisation=utilisation,
        estimated_queue_drain_ms=parallel_drain_ms,
        can_cook_any_dish=cookable,
    )


# ---------------------------------------------------------------------------
# Re-open logic
# ---------------------------------------------------------------------------

def should_reopen_restaurant(
    active_kitchen_queue: List[Dict[str, Any]],
    recipes: List[RecipeSchema],
    phase_time_remaining_ms: int,
    shadow_inventory: Dict[str, int],
    menu_dish_names: List[str],
    max_concurrent_dishes: int = 5,
    min_time_for_reopen_ms: int = 30_000,
) -> bool:
    """After closing, decide if it's worth re-opening.

    Re-open when:
    1. Kitchen queue has drained below 50% capacity.
    2. Enough phase time remains to cook at least one more dish.
    3. Ingredients are available for at least one menu dish.
    """
    queue_len = len(active_kitchen_queue)
    if queue_len > max_concurrent_dishes // 2:
        return False

    if phase_time_remaining_ms < min_time_for_reopen_ms:
        return False

    return any_dish_cookable(menu_dish_names, shadow_inventory, recipes)


# ---------------------------------------------------------------------------
# Customer prioritisation
# ---------------------------------------------------------------------------

def prioritise_customers(
    pending_customers: List[Dict[str, str]],
    menu_prices: Dict[str, int],
    shadow_inventory: Dict[str, int],
    recipes: List[RecipeSchema],
    dish_matcher: Optional[Dict[str, str]] = None,
) -> List[CustomerPriority]:
    """Rank pending customers by expected value.

    Parameters
    ----------
    pending_customers : list of {"client_name": str, "order_text": str}.
    menu_prices : mapping dish_name → price (from current menu).
    shadow_inventory : locally-tracked inventory.
    recipes : all game recipes.
    dish_matcher : optional pre-computed mapping client_name → best_dish.
        If None, the function skips revenue estimation (LLM must do matching).

    Returns
    -------
    List of CustomerPriority sorted by priority_score descending.
    """
    results: List[CustomerPriority] = []

    for cust in pending_customers:
        name = cust.get("client_name", cust.get("name", "unknown"))
        order = cust.get("order_text", cust.get("order", ""))

        matched_dish = dish_matcher.get(name) if dish_matcher else None
        revenue = menu_prices.get(matched_dish, 0) if matched_dish else 0

        # Quick intolerance risk flag (keyword heuristic — LLM should do the real check)
        intolerance_keywords = [
            "intoleran", "allerg", "cannot eat", "no ", "avoid",
            "sensitive to", "reaction to", "deadly", "fatal",
        ]
        has_risk = any(kw in order.lower() for kw in intolerance_keywords)

        # Priority: revenue potential, penalised if intolerance risk is high
        priority = float(revenue)
        if has_risk:
            priority *= 0.5  # downrank risky orders (need careful handling)
        if matched_dish and can_cook_dish(matched_dish, shadow_inventory, recipes):
            priority *= 1.2  # boost if we can actually cook it right now

        results.append(CustomerPriority(
            client_name=name,
            order_text=order,
            matched_dish=matched_dish,
            expected_revenue=revenue,
            priority_score=priority,
            has_intolerance_risk=has_risk,
        ))

    results.sort(key=lambda c: c.priority_score, reverse=True)
    return results
