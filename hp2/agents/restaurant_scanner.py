from __future__ import annotations

import logging

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    ClientOrder,
    GamePhase,
    GameStartedEvent,
    HackapizzaClient,
    IncomingMessage,
)


class RestaurantScanner(BaseAgent):
    """Agent that monitors which restaurants are open at each phase transition."""

    def __init__(self, client: HackapizzaClient | None = None):
        super().__init__(client)
        self.logger = logging.getLogger("RestaurantScanner")

    async def on_game_started(self, event: GameStartedEvent) -> None:
        # First snapshot as soon as the turn starts.
        await self._log_open_restaurants(trigger="game_started")

    async def on_phase_changed(self, phase: GamePhase) -> None:
        await self._log_open_restaurants(trigger=f"phase:{phase.value}")

    async def on_client_spawned(self, order: ClientOrder) -> None:
        pass

    async def on_preparation_complete(self, dish_name: str) -> None:
        pass

    async def on_new_message(self, message: IncomingMessage) -> None:
        pass

    async def _log_open_restaurants(self, trigger: str) -> None:
        try:
            restaurants = await self.client.get_restaurants()
        except Exception as exc:
            self.logger.warning("Failed to fetch restaurants (%s): %s", trigger, exc)
            return

        open_restaurants = [r for r in restaurants if getattr(r, "is_open", False)]
        names = ", ".join(r.name for r in open_restaurants) if open_restaurants else "none"

        self.logger.info(
            "[%s] Open restaurants: %d/%d -> %s",
            trigger,
            len(open_restaurants),
            len(restaurants),
            names,
        )


if __name__ == "__main__":
    import asyncio

    from hp2.core.api import HackapizzaClient

    agent = RestaurantScanner()

    asyncio.run(agent.run())
