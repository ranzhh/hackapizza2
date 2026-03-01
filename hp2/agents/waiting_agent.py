"""
WaitingAgent — Deterministic menu-publishing agent for the *waiting* phase.

This agent extends ``BaseAgent`` and implements **only** the ``phase_waiting``
logic.  It is fully deterministic (no LLM / agentic calls):

  1. Fetches the current restaurant state (inventory) via ``get_my_restaurant()``.
  2. Fetches all available recipes via ``get_recipes()``.
  3. Loads the *desired* recipe list from ``config.json``
     (located at the repository root).
  4. Filters recipes to those that are both *desired* (in configuration)
     **and** *feasible* (all ingredients present in inventory).
  5. Computes an integer price for each dish from the ingredient costs
     and the archetype multiplier declared in configuration.
  6. Publishes the resulting menu using ``save_menu`` as a direct
     MCP tool call (standalone, not via an agentic loop).

Configuration format
--------------------
``config.json`` is structured by **customer archetype**::

    {
        "recipes": {
            "<archetype>": {
                "recipes": ["<dish_name>", ...],
                "profit_multiplier": <float>
            },
            ...
        },
        "ingredients": {
            "bidding_price": <float>
        }
    }

The menu price for a dish = num_ingredients × bidding_price × profit_multiplier,
rounded to the nearest integer (minimum 1).  When a recipe appears under
multiple archetypes, the **highest** computed price is used.

Environment variables
---------------------
In live mode the agent reads credentials and connection strings from the
``.env`` file at the repository root.  The following variables are required:

  - ``HACKAPIZZA_TEAM_API_KEY``
  - ``HACKAPIZZA_TEAM_ID``
  - ``REGOLO_API_KEY``
  - ``EVENT_PROXY_URL``
  - ``HACKAPIZZA_SQL_CONNSTR``

Test mode
---------
Pass ``--test`` on the CLI (or ``test_mode=True`` in the constructor) to
run the entire waiting-phase pipeline against **mock data** — no server,
no SSE, no ``.env`` required.

Usage
-----
Run standalone (live)::

    python -m hp2.agents.waiting_agent

Run in test / dry-run mode::

    python -m hp2.agents.waiting_agent --test
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from hp2.agents.base import BaseAgent
from hp2.agents.bidding_agent import BiddingConfig
from hp2.core.api import (
    ClientOrder,
    GamePhase,
    GameStartedEvent,
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
# .env file path — resolved once relative to the repo root.
# The repo root is two levels up from this file:
#   hp2/agents/waiting_agent.py  →  ../../.env
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.json"


def _load_configuration(config_path: Path | None = None) -> BiddingConfig:
    """Load and validate the configuration file using Pydantic.

    Expected structure::

        {
            "recipes": {
                "<archetype>": {
                    "recipes": ["<dish_name>", ...],
                    "profit_multiplier": <float>
                },
                ...
            },
            "ingredients": {
                "bidding_price": <float>
            }
        }

    Returns a fully validated ``BiddingConfig`` instance.

    Raises ``FileNotFoundError`` if the file is missing, or
    ``pydantic.ValidationError`` if the content doesn't match the schema.
    """
    path = config_path or Path(os.environ.get("WAITING_AGENT_CONFIG", str(_DEFAULT_CONFIG_PATH)))
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found at {path}.  "
            "Create it or set WAITING_AGENT_CONFIG to the correct path."
        )

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    return BiddingConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Price computation from configuration
# ---------------------------------------------------------------------------


def _compute_recipe_price(
    num_ingredients: int,
    bidding_price: float,
    profit_multiplier: float,
) -> int:
    """Compute the integer menu price for a single recipe.

    price = round(num_ingredients × bidding_price × profit_multiplier),
    minimum 1.

    Parameters
    ----------
    num_ingredients :
        Number of ingredients in the recipe (from server recipe catalogue).
    bidding_price :
        Global bid price per ingredient from config.
    profit_multiplier :
        The archetype ``profit_multiplier`` from the ``"recipes"`` section.

    Returns
    -------
    int
        The menu price (>= 1).
    """
    total_cost = num_ingredients * bidding_price
    price = max(1, round(total_cost * profit_multiplier))
    return price


# ---------------------------------------------------------------------------
# Pure helper: build the candidate dish list from configuration
# ---------------------------------------------------------------------------


def _build_desired_dishes(
    config: BiddingConfig,
) -> Dict[str, float]:
    """Flatten the per-archetype configuration into a single dict of
    ``{recipe_name: best_profit_multiplier}`` across all archetypes.

    When a recipe appears under **multiple** archetypes (e.g., the same dish
    listed under both Saggi del Cosmo and Astrobarone), we keep the
    **highest** ``profit_multiplier`` — maximise expected revenue.

    Returns
    -------
    dict[str, float]
        Mapping from recipe name to its best profit multiplier.
    """
    best_multiplier: Dict[str, float] = {}

    for archetype_name, archetype_cfg in config.recipes.items():
        for dish_name in archetype_cfg.recipes:
            current = best_multiplier.get(dish_name, 0.0)
            if archetype_cfg.profit_multiplier > current:
                best_multiplier[dish_name] = archetype_cfg.profit_multiplier
                logger.debug(
                    "Dish '%s' (%s, ×%.2f) → new best multiplier",
                    dish_name,
                    archetype_name,
                    archetype_cfg.profit_multiplier,
                )

    return best_multiplier


# ---------------------------------------------------------------------------
# Pure helper: filter feasible recipes
# ---------------------------------------------------------------------------


def _compute_feasible_menu(
    desired_dishes: Dict[str, float],
    all_recipes: List[RecipeSchema],
    inventory: Dict[str, Any],
    bidding_price: float,
) -> List[MenuItem]:
    """Return ``MenuItem`` objects for recipes that are both *desired*
    (present in configuration) **and** *feasible* (every ingredient is
    present in inventory with qty >= 1).

    The menu price for each dish is computed as:
    ``num_ingredients × bidding_price × profit_multiplier``.

    Parameters
    ----------
    desired_dishes :
        Flattened ``{recipe_name: profit_multiplier}`` from
        ``_build_desired_dishes``.
    all_recipes :
        Full recipe catalogue from the server (``get_recipes()``).
    inventory :
        Post-auction inventory (``RestaurantSchema.inventory``).
    bidding_price :
        Global bid price per ingredient from ``config.ingredients``.

    Returns
    -------
    list[MenuItem]
        Dishes ready for ``save_menu``.
    """
    # Build a quick lookup: recipe_name → RecipeSchema
    recipe_lookup: Dict[str, RecipeSchema] = {r.name: r for r in all_recipes}

    feasible: List[MenuItem] = []

    for recipe_name, profit_multiplier in desired_dishes.items():
        # ── Step 1: Does this recipe exist on the server? ──────────────
        recipe = recipe_lookup.get(recipe_name)
        if recipe is None:
            logger.warning(
                "Recipe '%s' from config.json not found in the "
                "server recipe catalogue — skipping.",
                recipe_name,
            )
            continue

        # ── Step 2: Do we have every ingredient? ───────────────────────
        #     All recipe ingredient quantities are 1 (per the API docs).
        missing = [ing for ing in recipe.ingredients if inventory.get(ing, 0) < 1]

        if missing:
            logger.info(
                "Recipe '%s' is NOT feasible — missing ingredients: %s",
                recipe_name,
                missing,
            )
            continue

<<<<<<< Updated upstream
        # ── Step 3: Recipe is both desired and feasible ────────────────
        price = float(price) * (1.0 + (float(recipe.prestige)/100.0))
        feasible.append(MenuItem(name=recipe_name, price=int(price)))
=======
        # ── Step 3: Compute price and add to menu ──────────────────────
        price = _compute_recipe_price(
            num_ingredients=len(recipe.ingredients),
            bidding_price=bidding_price,
            profit_multiplier=profit_multiplier,
        )
        feasible.append(MenuItem(name=recipe_name, price=float(price)))
>>>>>>> Stashed changes
        logger.info(
            "Recipe '%s' is feasible — will be listed at price %d.",
            recipe_name,
            price,
        )

    return feasible


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class WaitingAgent(BaseAgent):
    """Deterministic agent that handles only the **waiting** phase.

    When the game phase transitions to ``waiting``, this agent:

    1. Pulls the latest restaurant state (including post-auction inventory).
    2. Loads desired recipes from ``config.json`` (per-archetype).
    3. Computes a price for each dish (ingredient costs × multiplier).
    4. Cross-references desires with actual inventory to find feasible dishes.
    5. Calls ``save_menu`` (MCP tool) to publish the menu.

    Parameters
    ----------
    client : HackapizzaClient or None
        Live client.  Ignored when ``test_mode=True``.
    config_path : Path or None
        Override for ``config.json`` location.
    test_mode : bool
        When True, uses ``_MockHackapizzaClient`` and ``MOCK_CONFIGURATION``.
    """

    def __init__(
        self,
        client: HackapizzaClient | None = None,
        config_path: Path | None = None,
        *,
        test_mode: bool = False,
    ):
        self.test_mode = test_mode
        self.logger = logging.getLogger("WaitingAgent")

        self._config_path = config_path
        super().__init__(client)

        # Step 4: Pre-load the configuration.
        self._config: BiddingConfig = _load_configuration(config_path)
        self.logger.info(
            "Loaded configuration with %d archetype(s).",
            len(self._config.recipes),
        )

    # ------------------------------------------------------------------
    # SSE event handlers
    # ------------------------------------------------------------------

    async def on_game_started(self, event: GameStartedEvent) -> None:
        self.logger.info("Game started — turn_id: %s", event.turn_id)

    async def on_phase_changed(self, phase: GamePhase) -> None:
        self.logger.info("Phase changed to: %s", phase.value)
        if phase == GamePhase.WAITING:
            await self.phase_waiting()
        else:
            self.logger.debug("Phase '%s' not handled — ignoring.", phase.value)

    async def on_client_spawned(self, order: ClientOrder) -> None:
        pass

    async def on_preparation_complete(self, dish_name: str) -> None:
        self.logger.debug("Preparation complete (ignored): %s", dish_name)

    async def on_new_message(self, message: IncomingMessage) -> None:
        self.logger.debug("Message from %s (ignored)", message.sender_name)

    # ------------------------------------------------------------------
    # Core waiting-phase logic (deterministic, no LLM)
    # ------------------------------------------------------------------

    async def phase_waiting(self) -> List[MenuItem]:
        """Execute the deterministic waiting-phase workflow.

        Steps
        -----
        1. **Fetch restaurant state** — ``get_my_restaurant()`` returns
           the post-auction inventory.
        2. **Fetch recipes** — ``get_recipes()`` returns the server catalogue.
        3. **Load configuration** — ``config.json`` declares desired
           recipes per archetype and per-ingredient costs + multipliers.
        4. **Build desired dishes** — flatten the archetype structure into
           a single ``{name: price}`` map, picking the highest price when
           a recipe appears under multiple archetypes.
        5. **Compute feasible menu** — keep only dishes whose ingredients
           are all present in inventory.
        6. **Publish menu** — call ``client.save_menu(items)`` (MCP tool).

        Returns
        -------
        list[MenuItem]
            The menu items that were published.
        """
        self.logger.info("=== WAITING PHASE START ===")

        # ── 1. Fetch current restaurant state (inventory) ──────────────
        try:
            my_restaurant = await self.client.get_my_restaurant()
        except Exception as exc:
            self.logger.error("Failed to fetch restaurant state: %s", exc)
            return []

        inventory = my_restaurant.inventory
        self.logger.info(
            "Restaurant '%s' | Balance: %.2f | Reputation: %.2f | Inventory items: %d",
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
            self.logger.error("Failed to fetch recipes: %s", exc)
            return []

        self.logger.info("Fetched %d recipe(s) from the server.", len(all_recipes))

        # ── 3. (Re)load configuration ──────────────────────────────────
        if not self.test_mode:
            try:
                self._config = _load_configuration(self._config_path)
            except Exception as exc:
                self.logger.warning(
                    "Could not reload configuration (%s); using cached version.",
                    exc,
                )

        # ── 4. Build desired dishes with best multipliers ──────────────
        #    Flatten the per-archetype structure.  For each recipe,
        #    keep the highest profit_multiplier across archetypes.
        desired_dishes: Dict[str, float] = _build_desired_dishes(self._config)
        self.logger.info(
            "Configuration yields %d unique desired dish(es): %s",
            len(desired_dishes),
            list(desired_dishes.items()),
        )

        # ── 5. Compute feasible menu ───────────────────────────────────
        menu_items: List[MenuItem] = _compute_feasible_menu(
            desired_dishes=desired_dishes,
            all_recipes=all_recipes,
            inventory=inventory,
            bidding_price=self._config.ingredients.bidding_price,
        )

        if not menu_items:
            self.logger.warning(
                "No feasible recipes found!  Menu will be empty — "
                "restaurant will be invisible to customers."
            )

        self.logger.info(
            "Feasible menu (%d dish(es)): %s",
            len(menu_items),
            [(m.name, m.price) for m in menu_items],
        )

        # ── 6. Publish the menu via save_menu (MCP tool) ───────────────
        #
        #   save_menu(items) sends a JSON-RPC POST to /mcp.
        #   items = [{"name": str, "price": int (>0)}, ...]
        #   This is a standalone function call, NOT an agentic tool use.
        try:
            result = await self.client.save_menu(menu_items)
            self.logger.info(
                "save_menu succeeded — published %d dish(es). Response: %s",
                len(menu_items),
                result,
            )
        except Exception as exc:
            self.logger.error("save_menu FAILED: %s", exc)
            return menu_items

        self.logger.info("=== WAITING PHASE COMPLETE ===")
        return menu_items

    # ------------------------------------------------------------------
    # Entry-point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the agent.

        In test mode runs ``phase_waiting()`` once and returns.
        In live mode connects to the SSE stream.
        """
        if self.test_mode:
            self.logger.info("[TEST MODE] Running phase_waiting() once…")
            menu = await self.phase_waiting()
            self.logger.info(
                "[TEST MODE] Done. Published: %s",
                [(m.name, m.price) for m in menu],
            )
        else:
            await super().run()


# ---------------------------------------------------------------------------
# Standalone entry-point with --test flag
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="WaitingAgent — deterministic menu publisher.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        default=False,
        help="Dry-run with mock data (no .env / server needed).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.json (live mode only).",
    )
    args = parser.parse_args()

    agent = WaitingAgent(config_path=args.config, test_mode=args.test)
    asyncio.run(agent.run())
