from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, List, Literal, Optional

from datapizza.clients.openai_like import OpenAILikeClient  # type: ignore

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    ClientOrder,
    GamePhase,
    GameStartedEvent,
    HackapizzaClient,
    IncomingMessage,
    PhaseChangedEvent,
)
from hp2.core.schema.models import MealSchema, RecipeSchema
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

_ORDER_WITH_INTOLERANCE_RE = re.compile(
    r"(?i)\b(?:i\s+want\s+to\s+eat|eat)\s+(?P<eat>.+?)\.?\s*(?:i\s*[’']?m\s+)?intolerant\s+to\s+(?P<intolerant>.+?)\.?\s*$"
)


@dataclass
class PendingOrder:
    """A customer order waiting to be fulfilled."""

    turn_id: str | None
    client_name: str
    order_text: str
    matched_dish: Optional[str] = None
    preparing: bool = False
    served: bool = False


@dataclass
class ServingAgentConfig:
    close_on_missing_ingredients_threshold: int = 1


class ServingAgent(BaseAgent):
    """Phase 4 (serving) agent.

    LLM is used ONLY to decide which dish to serve each customer
    (intolerance check + menu matching). Everything else is programmatic:
    - Opening the restaurant (update_restaurant_is_open)
    - Calling prepare_dish / serve_dish
    - Tracking inventory, pending orders, prepared dishes
    - Resolving client IDs from meals endpoint
    """

    def __init__(
        self,
        client: HackapizzaClient | None = None,
        config: ServingAgentConfig | None = None,
        log_only: bool = False,
    ):
        super().__init__(client)
        self.config = config or ServingAgentConfig()
        self.log_only = log_only

        if self.log_only:
            logger.info(
                "[INIT] ServingAgent initialized in LOG-ONLY mode. No actions will be executed, only logged."
            )

        settings = get_settings()

        # LLM client — single-shot completions, no agent/tools
        self.llm = OpenAILikeClient(
            api_key=settings.regolo_api_key,
            model="gpt-oss-120b",
            system_prompt="You are a helpful kitchen manager. Respond only with valid JSON.",
            base_url="https://api.regolo.ai/v1",
        )

        # Per-turn state
        self.pending_orders: List[PendingOrder] = []
        self.prepared_dishes: List[str] = []
        self.recipes: dict[str, RecipeSchema] = {}
        self.menu_items: set[str] = set()
        self.meals: List[MealSchema] = []  # type: ignore
        self.failed_serves: int = 0

    # ── Lifecycle hooks (only serving matters) ───────────────────────

    async def on_game_started(self, event: GameStartedEvent) -> None:
        self._reset_state()

    async def on_phase_changed(self, event: PhaseChangedEvent) -> None:
        if event.new_phase == GamePhase.SERVING:
            await self._load_menu()
            logger.info("[SERVING] Entering SERVING phase. Menu items: %s", self.menu_items)
            try:
                self.recipes = {
                    r.name: r for r in await self.client.get_recipes() if r.name in self.menu_items
                }
            except Exception as exc:
                logger.warning("[SERVING] Could not fetch recipes: %s", exc)
            logger.info("[SERVING] Loaded recipes: %s", list(self.recipes.keys()))
        elif event.new_phase == GamePhase.STOPPED:
            self._reset_state()

    async def on_client_spawned(self, order: ClientOrder) -> None:
        logger.debug("[CUSTOMER] name=%s: %s", order.client_name, order.order_text)
        pending = PendingOrder(
            turn_id=order.turn_id,
            client_name=order.client_name,
            order_text=order.order_text,
        )
        self.pending_orders.append(pending)
        await self._process_order(pending)

    async def on_preparation_complete(self, dish_name: str) -> None:
        logger.debug("[PREP DONE] %s", dish_name)
        await self._serve_ready_dish(dish_name)

    async def on_new_message(self, message: IncomingMessage) -> None:
        pass

    # ── Serving logic ────────────────────────────────────────────────

    async def _process_order(self, order: PendingOrder) -> None:
        """For each customer: ask LLM which dish, then prepare_dish programmatically."""
        if order.preparing or order.served:
            return

        if self.log_only:
            logger.info(f"[LOG-ONLY] Pending order received: {order}")
            return

        # ── Single LLM call: pick the dish ──
        dish_name = await _ask_llm_for_dish(self.llm, self.menu_items, list(self.recipes.values()), order)

        if dish_name is None:
            logger.warning("[SERVING] LLM decided not to serve %s.", order.client_name)
            return

        order.matched_dish = dish_name
        order.preparing = True

        # ── Programmatic: call prepare_dish ──
        try:
            await self.client.prepare_dish(dish_name=dish_name)
            logger.info("[SERVING] Cooking '%s' for %s.", dish_name, order.client_name)
        except Exception as exc:
            logger.error("[SERVING] prepare_dish failed for '%s': %s", dish_name, exc)
            order.preparing = False
            self.failed_serves += 1
            if self.failed_serves >= self.config.close_on_missing_ingredients_threshold:
                logger.warning(
                    "[SERVING] Too many failed serves (%d), closing restaurant.",
                    self.failed_serves,
                )
                try:
                    await self.client.set_restaurant_open_status(is_open=False)
                except Exception as exc:
                    logger.error("[SERVING] Failed to close restaurant: %s", exc)

    async def parse_order_message(
        self,
        order: str,
    ) -> Literal["INTOLERANCE", "UNKNOWN"] | str:
        order = order.strip()
        if not order:
            return "UNKNOWN"

        match = _ORDER_WITH_INTOLERANCE_RE.search(order)
        if match:
            eat = match.group("eat").strip()
            intolerant = match.group("intolerant").strip()

            if (
                eat in self.recipes
                and intolerant in self.recipes[eat].ingredients
            ):
                return "INTOLERANCE"

            else:
                return eat

        elif order in self.recipes:
                return order

        return "UNKNOWN"

    async def _serve_ready_dish(self, dish_name: str) -> None:
        """Dish is ready — find matching customer and serve. Fully programmatic."""
        if self.log_only:
            logger.info(f"[LOG-ONLY] Serving dish '{dish_name}'")
            return

        target: Optional[PendingOrder] = None
        for order in self.pending_orders:
            if order.matched_dish == dish_name and order.preparing and not order.served:
                target = order
                break

        if target is None:
            logger.warning("[SERVING] Dish '%s' ready but no matching customer.", dish_name)
            return

        # Resolve canonical client ID from meals endpoint
        resolved_id = await self._resolve_client_id(target)

        if not resolved_id:
            logger.warning(
                "[SERVING] Could not resolve client ID for %s — cannot serve.",
                target.client_name,
            )
            return

        try:
            await self.client.serve_dish(dish_name=dish_name, client_id=resolved_id)
            target.served = True
            logger.info(
                "[SERVED] '%s' -> %s (id=%s).",
                dish_name,
                target.client_name,
                resolved_id,
            )
        except Exception as exc:
            logger.error(
                "[SERVING] serve_dish failed '%s' -> %s: %s",
                dish_name,
                target.client_name,
                exc,
            )
            self.failed_serves += 1
            if self.failed_serves >= self.config.close_on_missing_ingredients_threshold:
                logger.warning(
                    "[SERVING] Too many failed serves (%d), closing restaurant.",
                    self.failed_serves,
                )
                try:
                    await self.client.set_restaurant_open_status(is_open=False)
                except Exception as exc:
                    logger.error("[SERVING] Failed to close restaurant: %s", exc)

    # ── Programmatic helpers ─────────────────────────────────────────

    async def _load_menu(self) -> None:
        """Pull latest recipes, menu, and inventory from the API."""
        try:
            restaurant = await self.client.get_my_restaurant()
            self.menu_items = {item.name for item in restaurant.menu.items}
        except Exception as exc:
            logger.warning("[SERVING] Could not fetch restaurant state: %s", exc)

    async def _resolve_client_id(self, order: PendingOrder) -> str | None:
        """Resolve canonical client ID from get_meals (typed MealSchema)."""

        if not order.turn_id:
            raise ValueError("Turn ID is not set — cannot resolve client ID")

        async def _resolve_client_id():
            for meal in self.meals:
                customer_name = meal.customer.name if meal.customer else None
                if customer_name == order.client_name and meal.request == order.order_text:
                    resolved = str(meal.customer_id)
                    logger.debug(
                        "[RESOLVE] %s -> %s (meal.customer_id)",
                        order.client_name,
                        resolved,
                    )
                    return resolved
            return None

        client_id = await _resolve_client_id()
        if client_id:
            return client_id

        # if we fail, we reset meals cache and try once more
        try:
            self.meals = await self.client.get_meals(order.turn_id)
            return await _resolve_client_id()
        except Exception:
            return None

    def _reset_state(self) -> None:
        self.pending_orders.clear()
        self.recipes.clear()
        self.menu_items.clear()
        self.meals.clear()


# ── LLM: single call per customer ────────────────────────────────


async def _ask_llm_for_dish(
    llm: Any, menu_items: set[str], recipes: list[RecipeSchema], order: PendingOrder
) -> Optional[str]:
    """Ask the LLM which dish to prepare. Returns dish name or None."""
    prompt = DISH_SELECTION_PROMPT.format(
        order_text=order.order_text,
        menu_json=json.dumps(list(menu_items), indent=2),
        recipes_json=json.dumps(recipes, indent=2, default=str),
    )

    try:
        response = await llm.a_invoke(prompt)
        text = str(response.text).strip()
        logger.debug("[LLM] Response for %s: %s", order.client_name, text)

        # Parse JSON response
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
            text = text.strip()

        data = json.loads(text)
        dish = data.get("dish_name")

        if not dish:
            logger.warning("[LLM] Not serving %s.", order.client_name)
            return None

        # Resolve exact menu name (case-insensitive)
        matched = None
        for item in menu_items:
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


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-only", action="store_true", help="Log actions without executing them"
    )
    args = parser.parse_args()

    agent = ServingAgent(log_only=args.log_only)

    asyncio.run(agent.run())
