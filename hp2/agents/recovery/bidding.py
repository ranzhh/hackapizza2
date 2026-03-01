import asyncio
import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict

from hp2.agents.base import BaseAgent
from hp2.core.api import BidRequest, GamePhase, GameStartedEvent, MenuItem
from hp2.core.schema.models import RecipeSchema

logging.basicConfig()


@dataclass
class MenuConfig:
    recipes: list[RecipeSchema]
    ingredients: DefaultDict[str, int]


class BiddingAgent(BaseAgent):
    """Agent responsible for bidding on client orders during the BIDDING phase."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config: MenuConfig | None = None

    async def on_game_started(self, event: GameStartedEvent):
        self.logger.info("[STARTED] Game started, turn %s", event.turn_id)
        config = await self._prepare_menu(n_recipes=1, n_times=10)
        self.logger.info(f"[STARTED] Prepared menu config: {config}")
        self._config = config

    async def on_phase_changed(self, phase: GamePhase):
        if phase == GamePhase.CLOSED_BID:
            await self._handle_closed_bid_phase()

        elif phase == GamePhase.WAITING:
            await self._save_menu()

        else:
            self._handle_unmanaged_phase(phase)

    async def _handle_closed_bid_phase(self) -> None:
        bids: list[BidRequest] = []
        if self._config is not None:
            for ing, qty in self._config.ingredients.items():
                bids.append(BidRequest(ingredient=ing, bid=2, quantity=qty))

            self.logger.info(
                f"[BIDDING] Submitted bids for ingredients: {self._config.ingredients}"
            )
            await self.client.submit_closed_bids(bids)

    async def _save_menu(self) -> None:
        self.logger.info("[WAITING] Entered WAITING phase - submitting menu config")
        if self._config is not None:
            # Create a menu for this phase
            menu_items = []
            for recipe in self._config.recipes:
                menu_items.append(
                    MenuItem(name=recipe.name, price=int((recipe.prestige + 1) * 1.0))
                )

            await self.client.save_menu(menu_items)

    def _handle_unmanaged_phase(self, phase: GamePhase) -> None:
        self.logger.info(f"{phase} Skipping phase {phase} - no action taken")

    async def on_start(self):
        if not self._config:
            self._config = await self._prepare_menu(5)

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
    agent = BiddingAgent()
    asyncio.run(agent.run())
