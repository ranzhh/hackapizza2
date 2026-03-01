import asyncio
import logging
import random
from collections import defaultdict

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    GamePhase,
    GameStartedEvent,
    PhaseChangedEvent,
    ClientOrder
)
from hp2.core.schema.models import RecipeSchema

logging.basicConfig()

MIN_STOCK = 1


class QuartermasterAgent(BaseAgent):
    """Agent responsible for administering the stock of items we need for client orders during the SERVING phase."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_game_started(self, event: GameStartedEvent):
        self.logger.info("[STARTED] Game started, turn %s", event.turn_id)

    async def on_phase_changed(self, event: PhaseChangedEvent):
        if event.new_phase == GamePhase.SERVING:
            pass

        else:
            self._handle_unmanaged_phase(event.new_phase)

    async def on_client_spawned(self, order: ClientOrder) -> 

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

    async def _prepare_menu(self, n_recipes: int = 1, n_times: int = 10) -> MenuConfig:
        """Prepare a menu configuration by selecting recipes and counting required ingredients."""
        recipes = await self.client.get_recipes()

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


if __name__ == "__main__":
    agent = QuartermasterAgent()
    asyncio.run(agent.run())
