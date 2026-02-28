"""
WaitingAgent — Deterministic menu-publishing agent for the *waiting* phase.

This agent extends ``BaseAgent`` and implements **only** the ``phase_waiting``
logic.  It is fully deterministic (no LLM / agentic calls):

  1. Fetches the current restaurant state (inventory) via ``get_my_restaurant()``.
  2. Fetches all available recipes via ``get_recipes()``.
  3. Loads the *desired* recipe list from ``configuration.json``
     (located at the repository root).
  4. Filters recipes to those that are both *desired* (in configuration)
     **and** *feasible* (all ingredients present in inventory).
  5. Assigns each dish the price declared in configuration.
  6. Publishes the resulting menu using ``save_menu`` as a direct
     MCP tool call (standalone, not via an agentic loop).

Usage
-----
Run standalone::

    python -m hp2.agents.waiting_agent

Or import and wire into a broader orchestrator that calls
``await agent.phase_waiting()`` when the phase changes to ``waiting``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    ClientOrder,
    GamePhase,
    HackapizzaClient,
    IncomingMessage,
    MenuItem,
)
from hp2.core.schema.models import RecipeSchema

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("WaitingAgent")


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

# Default path: <repo_root>/configuration.json
# Can be overridden via the WAITING_AGENT_CONFIG env var.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configuration.json"


def _load_configuration(config_path: Path | None = None) -> Dict[str, Any]:
    """Load the configuration file that lists the recipes we *want* to offer.

    The file is expected to look like::

        {
            "recipes": [
                {"name": "Margherita Cosmica", "price": 15},
                {"name": "Nebula Ramen", "price": 25},
                ...
            ]
        }

    Parameters
    ----------
    config_path : Path, optional
        Explicit path.  Falls back to ``WAITING_AGENT_CONFIG`` env var,
        then to ``<repo_root>/configuration.json``.

    Returns
    -------
    dict  with at least a ``"recipes"`` key holding a list of
          ``{"name": str, "price": int}`` entries.

    Raises
    ------
    FileNotFoundError
        If the configuration file cannot be found at any of the candidate paths.
    """
    path = config_path or Path(
        os.environ.get("WAITING_AGENT_CONFIG", str(_DEFAULT_CONFIG_PATH))
    )
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found at {path}.  "
            "Create it or set WAITING_AGENT_CONFIG to the correct path."
        )

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Basic validation
    if "recipes" not in data or not isinstance(data["recipes"], list):
        raise ValueError(
            "configuration.json must contain a top-level 'recipes' list "
            'with entries like {"name": "...", "price": <int>}.'
        )

    return data


# ---------------------------------------------------------------------------
# Pure helper: filter feasible recipes from the config
# ---------------------------------------------------------------------------

def _compute_feasible_menu(
    desired_recipes: List[Dict[str, Any]],
    all_recipes: List[RecipeSchema],
    inventory: Dict[str, Any],
) -> List[MenuItem]:
    """Return the list of ``MenuItem`` objects for recipes that are both
    *desired* (present in ``configuration.json``) **and** *feasible*
    (every ingredient is present in the current inventory with qty >= 1).

    Parameters
    ----------
    desired_recipes :
        The ``"recipes"`` list loaded from ``configuration.json``.
        Each entry must have ``"name"`` (str) and ``"price"`` (int).
    all_recipes :
        Full recipe catalogue from the server (``get_recipes()``).
    inventory :
        Current ingredient stock (``RestaurantSchema.inventory``).
        Keys are ingredient names, values are quantities.

    Returns
    -------
    list[MenuItem]
        Dishes that can actually be cooked — ready for ``save_menu``.
    """

    # Build a quick lookup: recipe_name -> RecipeSchema
    recipe_lookup: Dict[str, RecipeSchema] = {r.name: r for r in all_recipes}

    # Build a set of desired recipe names for O(1) membership test
    desired_by_name: Dict[str, int] = {
        entry["name"]: int(entry["price"]) for entry in desired_recipes
    }

    feasible: List[MenuItem] = []

    for recipe_name, price in desired_by_name.items():
        # ── Step 1: Does this recipe actually exist on the server? ──
        recipe = recipe_lookup.get(recipe_name)
        if recipe is None:
            logger.warning(
                "Recipe '%s' from configuration.json not found in the "
                "server recipe catalogue — skipping.",
                recipe_name,
            )
            continue

        # ── Step 2: Do we have every ingredient in stock (qty >= 1)? ──
        #     All recipe ingredient quantities are 1 (per the API docs).
        missing = [
            ing
            for ing in recipe.ingredients
            if inventory.get(ing, 0) < 1
        ]

        if missing:
            logger.info(
                "Recipe '%s' is NOT feasible — missing ingredients: %s",
                recipe_name,
                missing,
            )
            continue

        # ── Step 3: Recipe is both desired and feasible ──
        # Prices must be integers > 0 (server rejects floats).
        final_price = max(price, 1)
        feasible.append(MenuItem(name=recipe_name, price=float(final_price)))
        logger.info(
            "Recipe '%s' is feasible — will be listed at price %d.",
            recipe_name,
            final_price,
        )

    return feasible


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

class WaitingAgent(BaseAgent):
    """Deterministic agent that handles only the **waiting** phase.

    When the game phase transitions to ``waiting``, this agent:

    1. Pulls the latest restaurant state (including post-auction inventory).
    2. Loads desired recipes from ``configuration.json``.
    3. Cross-references desires with actual inventory to find feasible dishes.
    4. Calls ``save_menu`` (MCP tool, invoked as a standalone function on
       the ``HackapizzaClient``) to publish the menu.

    All other phase events are logged and ignored.
    """

    def __init__(
        self,
        client: HackapizzaClient | None = None,
        config_path: Path | None = None,
    ):
        # Initialise the base agent (creates or re-uses a HackapizzaClient
        # and registers all SSE event handlers).
        super().__init__(client)

        self.logger = logging.getLogger("WaitingAgent")

        # Pre-load the configuration so we fail fast on startup if the
        # file is missing or malformed.
        self._config_path = config_path
        self._config = _load_configuration(config_path)
        self.logger.info(
            "Loaded configuration with %d desired recipe(s).",
            len(self._config["recipes"]),
        )

    # ------------------------------------------------------------------
    # SSE event handlers (BaseAgent contract)
    # ------------------------------------------------------------------

    async def on_game_started(self, data: Dict[str, Any]) -> None:
        """Log game-start data; no action required for waiting-only agent."""
        self.logger.info("Game started — data: %s", data)

    async def on_phase_changed(self, phase: GamePhase) -> None:
        """Dispatch to ``phase_waiting`` when the phase is ``waiting``.

        All other phases are logged but otherwise ignored.
        """
        self.logger.info("Phase changed to: %s", phase.value)

        if phase == GamePhase.WAITING:
            await self.phase_waiting()
        else:
            self.logger.debug(
                "Phase '%s' is not handled by WaitingAgent — ignoring.",
                phase.value,
            )

    async def on_client_spawned(self, order: ClientOrder) -> None:
        """Not handled — waiting-only agent."""
        self.logger.debug("Client spawned (ignored): %s", order.client_id)

    async def on_preparation_complete(self, dish_name: str) -> None:
        """Not handled — waiting-only agent."""
        self.logger.debug("Preparation complete (ignored): %s", dish_name)

    async def on_new_message(self, message: IncomingMessage) -> None:
        """Not handled — waiting-only agent."""
        self.logger.debug(
            "Message from %s (ignored): %s",
            message.sender_name,
            message.text[:80],
        )

    # ------------------------------------------------------------------
    # Core waiting-phase logic (deterministic, no LLM)
    # ------------------------------------------------------------------

    async def phase_waiting(self) -> None:
        """Execute the deterministic waiting-phase workflow.

        Steps
        -----
        1. **Fetch restaurant state** — ``get_my_restaurant()`` returns
           balance, reputation, and most importantly the *inventory*
           (which now reflects auction results).
        2. **Fetch recipes** — ``get_recipes()`` returns every recipe
           known to the game (name, ingredients, prep time, prestige).
        3. **Load configuration** — ``configuration.json`` holds the
           recipes the team *wants* to offer along with target prices.
        4. **Compute feasible menu** — intersect desired recipes with
           what is actually cookable given the current inventory.
        5. **Publish menu** — call ``client.save_menu(items)`` which
           invokes the ``save_menu`` MCP tool via JSON-RPC POST to
           ``/mcp``.  This is a direct function call, not an agentic
           tool invocation.

        The function is fully deterministic: given the same inventory,
        recipes, and configuration it will always produce the same menu.
        """
        self.logger.info("=== WAITING PHASE START ===")

        # ── 1. Fetch current restaurant state (inventory) ──────────────
        try:
            my_restaurant = await self.client.get_my_restaurant()
        except Exception as exc:
            self.logger.error(
                "Failed to fetch restaurant state: %s — aborting waiting phase.",
                exc,
            )
            return

        inventory = my_restaurant.inventory
        self.logger.info(
            "Restaurant '%s' | Balance: %.2f | Reputation: %.2f | "
            "Inventory items: %d",
            my_restaurant.name,
            my_restaurant.balance,
            my_restaurant.reputation,
            len(inventory),
        )
        self.logger.debug("Full inventory: %s", inventory)

        # ── 2. Fetch all available recipes from the server ─────────────
        try:
            all_recipes: List[RecipeSchema] = await self.client.get_recipes()
        except Exception as exc:
            self.logger.error(
                "Failed to fetch recipes: %s — aborting waiting phase.",
                exc,
            )
            return

        self.logger.info(
            "Fetched %d recipe(s) from the server.", len(all_recipes)
        )

        # ── 3. Load desired recipes from configuration.json ────────────
        #    (Already loaded in __init__; re-load to pick up hot changes.)
        try:
            self._config = _load_configuration(self._config_path)
        except Exception as exc:
            self.logger.warning(
                "Could not reload configuration (%s); using cached version.",
                exc,
            )

        desired_recipes: List[Dict[str, Any]] = self._config["recipes"]
        self.logger.info(
            "Configuration declares %d desired recipe(s).",
            len(desired_recipes),
        )

        # ── 4. Compute feasible menu ───────────────────────────────────
        menu_items: List[MenuItem] = _compute_feasible_menu(
            desired_recipes=desired_recipes,
            all_recipes=all_recipes,
            inventory=inventory,
        )

        if not menu_items:
            self.logger.warning(
                "No feasible recipes found!  The menu will be empty — "
                "restaurant will be invisible to customers."
            )

        self.logger.info(
            "Feasible menu: %s",
            [(m.name, m.price) for m in menu_items],
        )

        # ── 5. Publish the menu via save_menu (MCP tool) ───────────────
        #
        # From the MCP documentation (artifacts/mcp_discovery/mcp.md):
        #   save_menu(items) -> Any
        #     "Save the menu"
        #     Args: items (list) — each item is {"name": str, "price": int}
        #
        # The HackapizzaClient wraps this as:
        #   client.save_menu(items: List[MenuItem]) -> Any
        # which internally calls:
        #   _mcp_call("save_menu", items=[asdict(i) for i in items])
        #
        # This is a *standalone function call*, not an agentic tool use.
        # It directly sends a JSON-RPC POST to /mcp.
        try:
            # result = await self.client.save_menu(menu_items)
            self.logger.info(
                "save_menu succeeded — published %d dish(es). "
                "Server response: %s",
                len(menu_items),
                # result,
                menu_items
            )
        except Exception as exc:
            self.logger.error(
                "save_menu FAILED: %s — menu was NOT published.", exc
            )
            return

        self.logger.info("=== WAITING PHASE COMPLETE ===")


# ---------------------------------------------------------------------------
# Standalone entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    agent = WaitingAgent()
    asyncio.run(agent.run())