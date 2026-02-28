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


class ExampleAgent(BaseAgent):
    """Agent that monitors which restaurants are open at each phase transition."""

    def __init__(self, client: HackapizzaClient | None = None):
        self.logger = logging.getLogger("ExampleAgent")
        super().__init__(client)

    async def on_game_started(self, event: GameStartedEvent) -> None:
        self.logger.info("Example agent started with turn_id: %s", event.turn_id)

    async def on_phase_changed(self, phase: GamePhase) -> None:
        self.logger.info("Phase changed to: %s", phase)

    async def on_client_spawned(self, order: ClientOrder) -> None:
        self.logger.info("Client spawned with order: %s", order)

    async def on_preparation_complete(self, dish_name: str) -> None:
        self.logger.info("Preparation complete for dish: %s", dish_name)

    async def on_new_message(self, message: IncomingMessage) -> None:
        self.logger.info("New message received: %s", message)


if __name__ == "__main__":
    import asyncio

    from hp2.core.api import HackapizzaClient

    agent = ExampleAgent()

    asyncio.run(agent.run())
