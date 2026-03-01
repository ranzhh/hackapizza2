from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import aiohttp

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    ClientOrder,
    GamePhase,
    GameStartedEvent,
    HackapizzaClient,
    IncomingMessage,
    PhaseChangedEvent,
)


class RestaurantManager(BaseAgent):
    """Tracks spawned/served pressure and auto-closes when service degrades."""

    def __init__(self, client: HackapizzaClient | None = None):
        super().__init__(client)
        self.logger = logging.getLogger("RestaurantManager")
        self.turn_id: str | None = None
        self.spawned = 0
        self.served = 0
        self.unserviceable = 0
        self.pending_serviceable = 0
        self._serving_task: asyncio.Task[None] | None = None
        self._is_serving_phase = False
        self._is_open = False
        self._recent_serviceable: deque[bool] = deque(maxlen=20)
        self._last_open_toggle_at = 0.0
        self._open_toggle_cooldown_s = 10.0

    async def on_game_started(self, event: GameStartedEvent) -> None:
        self.turn_id = event.turn_id
        self.logger.info("GAME STARTED - turn_id=%s", self.turn_id)
        await self._open_if_closed(trigger="game_start")

    async def on_start(self) -> None:
        """Best-effort startup sync when process boots mid-turn."""
        await self._open_if_closed(trigger="agent_start")
            
    async def on_phase_changed(self, event: PhaseChangedEvent) -> None:
        self.client.set_restaurant_open_status(is_open=True)
        if event.new_phase == GamePhase.SERVING:
            await self._enter_serving_phase()

            return

        self._is_serving_phase = False
        if event.new_phase not in [GamePhase.SERVING, GamePhase.STOPPED]:
            await self._open_if_closed(trigger=f"phase_change:{event.new_phase.value}")

    async def _enter_serving_phase(self) -> None:
        self._is_serving_phase = True
        self._reset_serving_metrics()

        if self._serving_task is None or self._serving_task.done():
            self.logger.info("SERVING started: launching background loop")
            self._serving_task = asyncio.create_task(self._serving_loop())
        else:
            self.logger.info("SERVING started: reusing background loop")

        try:
            status = await self.client.get_my_restaurant()
            self._is_open = bool(status.is_open)
        except Exception as exc:
            self.logger.warning("Failed to read current open status: %s", exc)

    def _reset_serving_metrics(self) -> None:
        self.spawned = 0
        self.served = 0
        self.unserviceable = 0
        self.pending_serviceable = 0
        self._recent_serviceable.clear()

    async def _open_if_closed(self, trigger: str) -> None:
        """Open restaurant only when currently closed; safe to call repeatedly."""
        try:
            status = await self.client.get_my_restaurant()
            self._is_open = bool(status.is_open)
        except Exception as exc:
            self.logger.warning("[%s] Failed to read current open status: %s", trigger, exc)
            return

        if self._is_open:
            self.logger.info("[%s] Restaurant already open", trigger)
            return

        try:
            await self.client.set_restaurant_open_status(is_open=True)
            self._is_open = True
            self.logger.info("[%s] Restaurant was closed, now opened", trigger)
        except Exception as exc:
            self.logger.warning(
                "[%s] Could not open restaurant (phase may forbid it): %s", trigger, exc
            )

    async def on_client_spawned(self, order: ClientOrder) -> None:
        if not self._is_serving_phase:
            return

        self.spawned += 1
        is_serviceable = await self._can_serve_order(order)
        self._recent_serviceable.append(is_serviceable)

        if is_serviceable:
            self.pending_serviceable += 1
        else:
            self.unserviceable += 1

        self.logger.info(
            "[ORDER] spawned=%s served=%s pending=%s serviceable=%s client=%s order=%s",
            self.spawned,
            self.served,
            self.pending_serviceable,
            is_serviceable,
            order.client_name,
            order.order_text,
        )
        await self._evaluate_open_policy(trigger="client_spawned")

    async def on_preparation_complete(self, dish_name: str) -> None:
        if not self._is_serving_phase:
            return

        if self.pending_serviceable > 0:
            self.served += 1
            self.pending_serviceable -= 1
        else:
            self.logger.warning(
                "[PREP] unmatched preparation_complete for dish=%s (spawned=%s served=%s pending=%s)",
                dish_name,
                self.spawned,
                self.served,
                self.pending_serviceable,
            )
            return

        self.logger.info(
            "[PREP] dish=%s spawned=%s served=%s pending=%s",
            dish_name,
            self.spawned,
            self.served,
            self.pending_serviceable,
        )
        await self._evaluate_open_policy(trigger="preparation_complete")

    async def on_new_message(self, message: IncomingMessage) -> None:
        pass

    async def _serving_loop(self) -> None:
        try:
            while True:
                if not self._is_serving_phase:
                    await asyncio.sleep(1)
                    continue

                served_ratio = (self.served / self.spawned) if self.spawned else 1.0
                status = await self.client.get_my_restaurant()
                self._is_open = bool(status.is_open)
                self.logger.info(
                    "[SERVING LOOP] is_open=%s spawned=%s served=%s pending=%s unserviceable=%s served_ratio=%.2f",
                    status.is_open,
                    self.spawned,
                    self.served,
                    self.pending_serviceable,
                    self.unserviceable,
                    served_ratio,
                )
                await self._evaluate_open_policy(trigger="loop_tick")
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            await self._close_restaurant_best_effort()
            self.logger.info("[SERVING LOOP] stopped")
            raise

    async def _can_serve_order(self, order: ClientOrder) -> bool:
        """Heuristic: serviceable if one menu dish can be made from inventory now."""
        try:
            restaurant = await self.client.get_my_restaurant()
            recipes = await self.client.get_recipes()
        except Exception as exc:
            self.logger.warning("Cannot evaluate order serviceability: %s", exc)
            return False

        menu_names = [item.name for item in restaurant.menu.items]
        if not menu_names:
            return False

        recipes_by_name = {r.name: r for r in recipes}
        inventory = restaurant.inventory
        order_text = order.order_text.lower()
        preferred = [name for name in menu_names if name.lower() in order_text]
        candidates = preferred or menu_names

        for dish_name in candidates:
            recipe = recipes_by_name.get(dish_name)
            if recipe is None:
                continue
            if all(float(inventory.get(ing, 0)) >= qty for ing, qty in recipe.ingredients.items()):
                self.logger.info(
                    "Order '%s' is serviceable with dish '%s'", order.order_text, dish_name
                )
                return True

        self.logger.info("Order '%s' is NOT serviceable", order.order_text)
        return False

    async def _evaluate_open_policy(self, trigger: str) -> None:
        if not self._is_serving_phase:
            return

        now = time.monotonic()
        if (now - self._last_open_toggle_at) < self._open_toggle_cooldown_s:
            return

        recent_total = len(self._recent_serviceable)
        recent_unserviceable = sum(1 for x in self._recent_serviceable if not x)
        recent_unserviceable_ratio = recent_unserviceable / recent_total if recent_total else 0.0
        served_ratio = (self.served / self.spawned) if self.spawned else 1.0
        backlog = max(self.spawned - self.served, 0)

        should_close = (
            (self.spawned >= 6 and served_ratio < 0.45 and backlog >= 4)
            or (recent_total >= 5 and recent_unserviceable_ratio >= 0.60)
            or self.pending_serviceable >= 8
        )
        if should_close and self._is_open:
            self.logger.warning(
                "Policy triggered: should CLOSE (spawned=%s served=%s backlog=%s served_ratio=%.2f recent_unserviceable_ratio=%.2f pending_serviceable=%s)",
                self.spawned,
                self.served,
                backlog,
                served_ratio,
                recent_unserviceable_ratio,
                self.pending_serviceable,
            )

            # TODO: enable
            # await self.client.set_restaurant_open_status(is_open=False)

    async def shutdown(self) -> None:
        """Graceful stop: cancel loops and close restaurant before exit."""
        if self._serving_task and not self._serving_task.done():
            self._serving_task.cancel()
            try:
                await self._serving_task
            except asyncio.CancelledError:
                pass

        await self._close_restaurant_best_effort()

    async def _close_restaurant_best_effort(self) -> None:
        """Attempt to close restaurant, creating a temporary HTTP session if needed."""
        self.logger.info("Attempting to close restaurant before shutdown")

        if self.client._session is not None:
            try:
                await self.client.set_restaurant_open_status(is_open=False)
                self._is_open = False
                self.logger.info("Restaurant closed")
            except Exception as exc:
                self.logger.warning("Failed to close restaurant with existing session: %s", exc)
            return

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.client._headers) as session:
            self.client._session = session
            try:
                await self.client.set_restaurant_open_status(is_open=False)
                self._is_open = False
                self.logger.info("Restaurant closed")
            except Exception as exc:
                self.logger.warning("Failed to close restaurant on shutdown: %s", exc)
            finally:
                self.client._session = None


if __name__ == "__main__":
    agent = RestaurantManager()
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        agent.logger.warning("Ctrl+C received, shutting down gracefully")
        try:
            asyncio.run(agent.shutdown())
        except Exception as exc:
            agent.logger.warning("Shutdown encountered an error: %s", exc)
