import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict
import random

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    BidRequest,
    GamePhase,
    GameStartedEvent,
    IncomingMessage,
    MenuItem,
    PhaseChangedEvent,
)
from hp2.core.schema.models import RecipeSchema
from tools.api_unused import get_dish_stats

from tools.find_unused_ingredients import get_bids, get_avg_bid_item

logging.basicConfig()

RECIPES_WANTED = 20
N_TIMES = 3


@dataclass
class MenuConfig:
    recipes: list[RecipeSchema]
    ingredients: DefaultDict[str, int]


class BiddingAgent(BaseAgent):
    """Agent responsible for bidding on client orders during the BIDDING phase."""

    DEFAULT_BID_PRICE = 2  # fallback when no historical avg is available

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config: MenuConfig | None = None
        self.inventory: dict[str, int] | None = {}
        self._bid_matrix: dict | None = None

    async def on_game_started(self, event: GameStartedEvent):
        self.logger.info("[STARTED] Game started, turn %s", event.turn_id)
        config = await self._prepare_menu(n_recipes=RECIPES_WANTED, n_times=N_TIMES)
        self.logger.info(
            "[STARTED] Prepared menu config: %s", ", ".join([x.name for x in config.recipes])
        )
        self._config = config

        # Pre-fetch bid history so it's ready before the bidding phase
        await self._prefetch_bids()

    async def on_phase_changed(self, event: PhaseChangedEvent):
        if event.new_phase == GamePhase.CLOSED_BID:
            await self._handle_closed_bid_phase()

        elif event.new_phase == GamePhase.WAITING:
            await self._save_menu()

        else:
            self._handle_unmanaged_phase(event.new_phase)

    async def on_new_message(self, message: IncomingMessage) -> None:
        if message.sender_name == "server" and "try to buy" in message.text:
            await self._save_menu()

    async def _prefetch_bids(self) -> None:
        """Fetch historical bid data so we can price ingredients at the market average."""
        try:
            result = await get_bids(turn_id=None)
            if result.get("error"):
                self.logger.warning("[BIDS] Pre-fetch returned error: %s", result["error"])
                self._bid_matrix = None
            else:
                self._bid_matrix = result.get("bids", {})
                self.logger.info(
                    "[BIDS] Pre-fetched bid matrix with %d ingredients",
                    len(self._bid_matrix),
                )
        except Exception as exc:
            self.logger.warning("[BIDS] Pre-fetch failed: %s", exc)
            self._bid_matrix = None

    async def _handle_closed_bid_phase(self) -> None:
        bids: list[BidRequest] = []
        if self._config is not None:
            for ing, qty in self._config.ingredients.items():
                avg_price = (
                    get_avg_bid_item(self._bid_matrix, ing)
                    if self._bid_matrix
                    else None
                )
                bid_price = int(avg_price) if avg_price is not None else self.DEFAULT_BID_PRICE
                self.logger.info(
                    "[BIDDING] %s: qty=%d, bid=%d (avg=%s)",
                    ing, qty, bid_price,
                    f"{avg_price:.1f}" if avg_price is not None else "N/A",
                )
                bids.append(BidRequest(ingredient=ing, bid=bid_price, quantity=qty))

            self.logger.info("[BIDDING] Submitted bids for ingredients")
            await self.client.submit_closed_bids(bids)

    async def _save_menu(self) -> None:
        self.logger.info("[MENU] Entered phase - submitting menu config")
        await self._update_inventory()

        if self._config and self.inventory:
            # Create a menu for this phase
            menu_items: list[MenuItem] = []
            for recipe in self._config.recipes:
                if await self._validate_recipe(recipe):
                    menu_items.append(
                        MenuItem(name=recipe.name, price=int((recipe.prestige + 1) * 1.0))
                    )
                    self.logger.info("Added recipe %s", recipe.name)
                else:
                    self.logger.warning("Skipped recipe %s due to no inv", recipe.name)

            await self.client.save_menu(menu_items)
            self.logger.info(
                f"[MENU] Submitted menu with items: {[item.name for item in menu_items]}"
            )

    async def _update_inventory(self) -> None:
        self.logger.info("Updating inventory...")
        try:
            my_restaurant = await self.client.get_my_restaurant()
            if my_restaurant.inventory:
                self.inventory = my_restaurant.inventory
                self.logger.info(
                    "Inventory %s", "\n".join(f"{k}: {v}" for k, v in self.inventory.items())
                )
            else:
                raise ValueError("Inventory is None")

        except Exception:
            self.logger.exception("Exception updating inventory")

    async def _validate_recipe(self, recipe: RecipeSchema) -> bool:
        if not self.inventory:
            return False

        for ing, amount in recipe.ingredients.items():
            if self.inventory.get(ing, 0) < amount:
                return False

        return True

    def _handle_unmanaged_phase(self, phase: GamePhase) -> None:
        self.logger.info(f"{phase} Skipping phase {phase} - no action taken")

    async def on_start(self):
        if not self._config:
            self._config = await self._prepare_menu(n_recipes=RECIPES_WANTED, n_times=N_TIMES)

        try:
            await self._handle_closed_bid_phase()
        except Exception as exc:
            self.logger.warning(
                "[STARTUP] Closed bid handler failed during startup warmup: %s", exc
            )

        try:
            await self._save_menu()
        except Exception as exc:
            self.logger.warning("[STARTUP] Waiting handler failed during startup warmup: %s", exc)

        return await super().on_start()

    async def _prepare_menu(self, n_recipes: int = 10, n_times: int = 10, random_pool = True) -> MenuConfig:
        """Prepare a menu by ranking recipes on normalised prestige + menu frequency."""
        recipes = await self.client.get_recipes()
        if random_pool:
            self.logger.info("Got some recipes...")

            conf = MenuConfig(recipes=[], ingredients=defaultdict(int))

            # Choose recipes from the available ones
            chosen_recipes = random.sample(recipes, k=min(n_recipes, len(recipes)))
            conf.recipes = chosen_recipes
            self.logger.info("Recipes chosen:\n%s", "\n".join(["\t" + x.name for x in conf.recipes]))

            # Get all ingredients from the chosen recipes
            for recipe in chosen_recipes:
                for ingredient in recipe.ingredients:
                    conf.ingredients[ingredient] += n_times

            self.logger.info(
                "Ingredients required:\n%s", "\n".join(["\t" + x for x in conf.ingredients])
            )

            return conf
        
        self.logger.info("Got %d recipes", len(recipes))

        # Fetch how often each dish appears on other restaurants' menus
        dish_stats = await get_dish_stats()
        self.logger.info("Got dish stats for %d dishes", len(dish_stats))

        # Compute normalisation ceilings
        max_prestige = max((r.prestige for r in recipes), default=1) or 1
        max_occurrences = (
            max((d["times_on_menu"] for d in dish_stats.values()), default=1) or 1
        )

        # Score each recipe: norm_prestige + norm_occurrences
        scored: list[tuple[float, RecipeSchema]] = []
        for recipe in recipes:
            norm_prestige = recipe.prestige / max_prestige
            occurrences = dish_stats.get(recipe.name, {}).get("times_on_menu", 0)
            norm_occurrences = occurrences / max_occurrences
            score = norm_prestige + norm_occurrences
            scored.append((score, recipe))
            self.logger.debug(
                "  %s  prestige=%.2f  occ=%.2f  score=%.3f",
                recipe.name, norm_prestige, norm_occurrences, score,
            )

        # Sort descending and pick top n_recipes
        scored.sort(key=lambda t: t[0], reverse=True)
        chosen_recipes = [recipe for _, recipe in scored[:n_recipes]]

        conf = MenuConfig(recipes=chosen_recipes, ingredients=defaultdict(int))
        self.logger.info(
            "Recipes chosen (top %d):\n%s",
            n_recipes,
            "\n".join(
                f"\t{s:.3f}  {r.name}" for s, r in scored[:n_recipes]
            ),
        )

        for recipe in chosen_recipes:
            for ingredient in recipe.ingredients:
                conf.ingredients[ingredient] += n_times

        self.logger.info(
            "Ingredients required:\n%s",
            "\n".join(f"\t{k}: {v}" for k, v in conf.ingredients.items()),
        )

        return conf


if __name__ == "__main__":
    agent = BiddingAgent()
    asyncio.run(agent.run())
