from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from datapizza.clients.openai_like import OpenAILikeClient  # type: ignore

from hp2.agents.base import BaseAgent
from hp2.core.api import ClientOrder, GamePhase, GameStartedEvent, HackapizzaClient, IncomingMessage
from hp2.core.schema.models import RecipeSchema
from hp2.core.settings import get_settings

logger = logging.getLogger("ServingAgent")

# ── LLM prompt ───────────────────────────────────────────────────────────

DISH_SELECTION_PROMPT = """\
You are the kitchen manager of a restaurant in Hackapizza 2.0.

A customer just arrived. You must decide which dish to prepare for them,
or decide NOT to serve them at all.

## Customer order
Request: "{order_text}"

## Our menu (dishes we can sell)
{menu_json}

## All recipes (with ingredients needed)
{recipes_json}

## RULES
1. If the customer's request describes a SPECIFIC dish only select a dish that
   genuinely matches what the customer is asking for. Do NOT pick "the closest
   alternative" or a substitute — if we don't have what they want, respond null.
2. The dish MUST be in our menu.
3. INTOLERANCE CHECK: Read the order carefully for intolerance / allergy hints.
   - If the customer mentions an intolerance, match their request only considering
     dishes that do NOT contain the offending ingredient(s). If we cannot satisfy such order, respond null.
4. If NO matching dish exists, respond null. Do NOT guess or approximate.

Respond with ONLY a JSON object, no markdown, no explanation:
{{"dish_name": "<exact dish name from menu>"}}
or
{{"dish_name": null}}
"""


@dataclass
class PendingOrder:
    """A customer order waiting to be fulfilled."""
    client_name: str
    order_text: str
    matched_dish: Optional[str] = None
    preparing: bool = False
    served: bool = False


class ServingAgent(BaseAgent):
    """Phase 4 (serving) agent.

    LLM is used ONLY to decide which dish to serve each customer
    (intolerance check + menu matching). Everything else is programmatic:
    - Opening the restaurant (update_restaurant_is_open)
    - Calling prepare_dish / serve_dish
    - Tracking inventory, pending orders, prepared dishes
    - Resolving client IDs from meals endpoint
    """

    def __init__(self, client: HackapizzaClient | None = None):
        super().__init__(client)
        self._init_serving_state()

    def _init_serving_state(self) -> None:
        """Initialise LLM client and per-turn state."""
        settings = get_settings()

        # LLM client — single-shot completions, no agent/tools
        self.llm = OpenAILikeClient(
            api_key=settings.regolo_api_key,
            model="gpt-oss-120b",
            system_prompt="You are a helpful kitchen manager. Respond only with valid JSON.",
            base_url="https://api.regolo.ai/v1",
        )

        # Per-turn state
        self.current_phase: Optional[GamePhase] = None
        self.turn_id: Optional[str] = None
        self.pending_orders: List[PendingOrder] = []
        self.prepared_dishes: List[str] = []
        self.shadow_inventory: Dict[str, int] = {}
        self.recipes: List[RecipeSchema] = []
        self.menu_items: List[str] = []

    @classmethod
    def create(cls, client: HackapizzaClient) -> "ServingAgent":
        """Create a ServingAgent that shares an existing client.

        Skips BaseAgent.__init__ so no SSE handlers are registered —
        use this when embedding inside another orchestrator (e.g. main.py).
        """
        instance = object.__new__(cls)
        instance.client = client
        instance._init_serving_state()
        return instance

    # ── Lifecycle hooks (only serving matters) ───────────────────────

    async def on_game_started(self, event: GameStartedEvent) -> None:
        self.turn_id = event.turn_id
        self._reset_state()

    async def on_phase_changed(self, phase: GamePhase) -> None:
        self.current_phase = phase

        if phase == GamePhase.SERVING:
            await self._enter_serving()
        elif phase == GamePhase.STOPPED:
            self._reset_state()

    async def on_client_spawned(self, order: ClientOrder) -> None:
        if self.current_phase != GamePhase.SERVING:
            return

        logger.info("[CUSTOMER] name=%s: %s", order.client_name, order.order_text)
        pending = PendingOrder(
            client_name=order.client_name,
            order_text=order.order_text,
        )
        self.pending_orders.append(pending)
        await self._process_order(pending)

    async def on_preparation_complete(self, dish_name: str) -> None:
        if self.current_phase != GamePhase.SERVING:
            return

        self.prepared_dishes.append(dish_name)
        logger.info("[PREP DONE] %s", dish_name)
        await self._serve_ready_dish(dish_name)

    async def on_new_message(self, message: IncomingMessage) -> None:
        pass

    # ── Serving logic ────────────────────────────────────────────────

    async def _enter_serving(self) -> None:
        """Phase start: open restaurant + refresh context"""
        # # 1. Open the restaurant
        # try:
        #     await self.client.set_restaurant_open_status(is_open=True)
        #     logger.info("[SERVING] Restaurant opened (update_restaurant_is_open).")
        # except Exception as exc:
        #     logger.error("[SERVING] Failed to open restaurant: %s", exc)

        # 2. Refresh recipes, menu, inventory
        try:
            self.recipes = await self.client.get_recipes()
        except Exception as exc:
            logger.warning("[SERVING] Could not fetch recipes: %s", exc)
        await self._refresh_inventory()

        # 3. Process any orders that queued before the phase started
        for order in self.pending_orders:
            if not order.preparing and not order.served:
                await self._process_order(order)

    async def _process_order(self, order: PendingOrder) -> None:
        """For each customer: ask LLM which dish, then prepare_dish programmatically."""
        if order.preparing or order.served:
            return

        # Refresh inventory before deciding
        await self._refresh_inventory()

        # ── Single LLM call: pick the dish ──
        dish_name = await self._ask_llm_for_dish(order)

        if dish_name is None:
            logger.warning("[SERVING] LLM decided not to serve %s.", order.client_name)
            return

        # Verify we have the recipe and ingredients (programmatic safety net)
        recipe = self._find_recipe(dish_name)
        if recipe is None:
            logger.warning("[SERVING] No recipe found for '%s' — skipping %s. Available recipes: %s",
                           dish_name, order.client_name, [r.name for r in self.recipes])
            return
        if not self._has_ingredients(recipe):
            logger.warning("[SERVING] Not enough ingredients for '%s' — skipping %s.", dish_name, order.client_name)
            return

        # Deduct from shadow inventory
        if recipe:
            for ing, qty in recipe.ingredients.items():
                self.shadow_inventory[ing] = self.shadow_inventory.get(ing, 0) - qty

        order.matched_dish = dish_name
        order.preparing = True

        # ── Programmatic: call prepare_dish ──
        try:
            await self.client.prepare_dish(dish_name=dish_name)
            logger.info("[SERVING] Cooking '%s' for %s.", dish_name, order.client_name)
        except Exception as exc:
            logger.error("[SERVING] prepare_dish failed for '%s': %s", dish_name, exc)
            order.preparing = False

    async def _serve_ready_dish(self, dish_name: str) -> None:
        """Dish is ready — find matching customer and serve. Fully programmatic."""
        target: Optional[PendingOrder] = None
        for order in self.pending_orders:
            if order.matched_dish == dish_name and order.preparing and not order.served:
                target = order
                break

        if target is None:
            logger.warning("[SERVING] Dish '%s' ready but no matching customer.", dish_name)
            return

        # Verify the dish is actually in our menu and has a valid recipe
        if dish_name not in self.menu_items:
            logger.warning("[SERVING] Dish '%s' not in menu — refusing to serve.", dish_name)
            return

        recipe = self._find_recipe(dish_name)
        if recipe is None:
            logger.warning("[SERVING] Dish '%s' has no known recipe — refusing to serve.", dish_name)
            return

        # Resolve canonical client ID from meals endpoint
        resolved_id = await self._resolve_client_id(target)

        if not resolved_id:
            logger.warning("[SERVING] Could not resolve client ID for %s — cannot serve.", target.client_name)
            return

        try:
            await self.client.serve_dish(dish_name=dish_name, client_id=resolved_id)
            target.served = True
            logger.info("[SERVED] '%s' -> %s (id=%s).", dish_name, target.client_name, resolved_id)
        except Exception as exc:
            logger.error("[SERVING] serve_dish failed '%s' -> %s: %s", dish_name, target.client_name, exc)

    # ── LLM: single call per customer ────────────────────────────────

    async def _ask_llm_for_dish(self, order: PendingOrder) -> Optional[str]:
        """Ask the LLM which dish to prepare. Returns dish name or None."""
        prompt = DISH_SELECTION_PROMPT.format(
            order_text=order.order_text,
            menu_json=json.dumps(self.menu_items, indent=2),
            recipes_json=json.dumps(self.recipes, indent=2, default=str),
        )

        try:
            response = await self.llm.a_invoke(prompt)
            text = str(response.text).strip()
            logger.info("[LLM] Response for %s: %s", order.client_name, text)

            # Parse JSON response
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]
                text = text.strip()

            data = json.loads(text)
            dish = data.get("dish_name")

            if not dish:
                logger.info("[LLM] Not serving %s.", order.client_name)
                logger.warning("[LLM] Prompt: %s", prompt)
                return None

            # Resolve exact menu name (case-insensitive)
            matched = None
            for item in self.menu_items:
                if item.lower() == dish.lower():
                    matched = item
                    break

            if not matched:
                logger.warning("[LLM] Dish '%s' not in menu — ignoring.", dish)
                return None

            return matched

        except json.JSONDecodeError as exc:
            logger.error("[LLM] Invalid JSON from LLM: %s", exc)
            return None
        except Exception as exc:
            logger.error("[LLM] Call failed: %s", exc)
            return None

    # ── Programmatic helpers ─────────────────────────────────────────

    async def _refresh_inventory(self) -> None:
        """Pull latest recipes, menu, and inventory from the API."""
        try:
            restaurant = await self.client.get_my_restaurant()
            self.shadow_inventory = restaurant.inventory
            menu = restaurant.menu
            items = menu.items
            self.menu_items = [item.name for item in items]
        except Exception as exc:
            logger.warning("[SERVING] Could not fetch restaurant state: %s", exc)

    async def _resolve_client_id(self, order: PendingOrder) -> str | None:
        """Resolve canonical client ID from get_meals (typed MealSchema)."""

        if not self.turn_id:
            raise ValueError("Turn ID is not set — cannot resolve client ID")
        try:
            meals = await self.client.get_meals(self.turn_id)
        except Exception as exc:
            logger.error("[RESOLVE] get_meals failed for turn=%s: %s", self.turn_id, exc)
            return None

        for meal in meals:
            customer_name = meal.customer.name if meal.customer else None
            if customer_name == order.client_name:
                resolved = str(meal.customer_id)
                logger.info("[RESOLVE] %s -> %s (meal.customer_id)", order.client_name, resolved)
                return resolved

        available_names = [m.customer.name for m in meals if m.customer]
        logger.warning("[RESOLVE] '%s' not found in meals. Available: %s", order.client_name, available_names)
        return None

    def _find_recipe(self, dish_name: str) -> Optional[RecipeSchema]:
        for r in self.recipes:
            if r.name.lower() == dish_name.lower():
                return r
        return None

    def _has_ingredients(self, recipe: RecipeSchema) -> bool:
        missing = []
        for ing, qty in recipe.ingredients.items():
            available = self.shadow_inventory.get(ing, 0)
            if available < qty:
                missing.append(f"{ing}: need {qty}, have {available}")
        if missing:
            logger.warning("[INVENTORY] Missing for '%s': %s", recipe.name, "; ".join(missing))
            return False
        return True

    def _reset_state(self) -> None:
        self.pending_orders.clear()
        self.prepared_dishes.clear()
        self.shadow_inventory.clear()
        self.recipes.clear()
        self.menu_items.clear()


if __name__ == "__main__":
    import asyncio

    agent = ServingAgent()

    asyncio.run(agent.run())
