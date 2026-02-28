from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from hp2.core.api import (
    ClientOrder,
    GamePhase,
    GameStartedEvent,
    HackapizzaClient,
    IncomingMessage,
)
from hp2.core.settings import get_settings, get_sql_logging_settings

logging.basicConfig(level=logging.INFO)


class BaseAgent:
    """Minimal event-driven agent scaffold for Hackapizza SSE events."""

    def __init__(self, client: HackapizzaClient | None = None):
        settings = get_settings()
        sql_settings = get_sql_logging_settings()

        self.client = client or HackapizzaClient(
            team_id=settings.hackapizza_team_id,
            api_key=settings.hackapizza_team_api_key,
            enable_sql_logging=True,
            sql_connstr=sql_settings.hackapizza_sql_connstr,
        )
        self._log_connstr = (
            getattr(self.client, "_log_connstr", None)
            or get_sql_logging_settings().hackapizza_sql_connstr
        )
        self._log_engine = create_engine(self._log_connstr, future=True)
        self._log_session_factory = sessionmaker(
            bind=self._log_engine, autoflush=False, autocommit=False, future=True
        )
        self._register_event_handlers()

    def _register_event_handlers(self) -> None:
        @self.client.on_game_started
        async def _on_game_started(event: GameStartedEvent) -> None:
            await self.on_game_started(event)

        @self.client.on_phase_changed
        async def _on_phase_changed(phase: GamePhase) -> None:
            await self.on_phase_changed(phase)

        @self.client.on_client_spawned
        async def on_client_spawned(order: ClientOrder) -> None:
            await self.on_client_spawned(order)

        @self.client.on_preparation_complete
        async def _on_preparation_complete(dish_name: str) -> None:
            await self.on_preparation_complete(dish_name)

        @self.client.on_new_message
        async def _on_new_message(message: IncomingMessage) -> None:
            await self.on_new_message(message)

    async def on_game_started(self, event: GameStartedEvent) -> None:
        raise NotImplementedError("Override on_game_started() in your agent.")

    async def on_phase_changed(self, phase: GamePhase) -> None:
        raise NotImplementedError("Override on_phase_changed() in your agent.")

    async def on_client_spawned(self, order: ClientOrder) -> None:
        raise NotImplementedError("Override on_client_order() in your agent.")

    async def on_preparation_complete(self, dish_name: str) -> None:
        raise NotImplementedError("Override on_preparation_complete() in your agent.")

    async def on_new_message(self, message: IncomingMessage) -> None:
        raise NotImplementedError("Override on_new_message() in your agent.")

    async def run(self) -> None:
        """Start consuming events from the Hackapizza event stream."""
        self.client.logger.info("Starting agent %s...", self.__class__.__name__)
        try:
            await self.client.start()
        finally:
            self._log_engine.dispose()

    def query_logging_db(
        self, query: str, params: Dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        with self._log_session_factory() as session:
            result = session.execute(text(query), params or {})
            return [dict(row._mapping) for row in result]

    async def aquery_logging_db(
        self, query: str, params: Dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.query_logging_db, query, params)


if __name__ == "__main__":
    import asyncio

    from hp2.core.api import HackapizzaClient

    agent = BaseAgent()

    asyncio.run(agent.run())
