from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    BidRequest,
    ClientOrder,
    GamePhase,
    GameStartedEvent,
    HackapizzaClient,
    IncomingMessage,
)
from hp2.core.schema.models import RecipeSchema


# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------


class ArchetypeConfig(BaseModel):
    """Configuration for a single customer archetype."""

    recipes: list[str] = Field(..., min_length=1, description="List of dish names available for this archetype.")
    profit_multiplier: float = Field(..., gt=0, description="Multiplier applied to profit for dishes served to this archetype.")


class IngredientsConfig(BaseModel):
    """Global ingredient bidding configuration."""

    bidding_price: float = Field(..., gt=0, description="Default bid price for all ingredients.")


class BiddingConfig(BaseModel):
    """Top-level configuration loaded from ``config.json``."""

    recipes: dict[str, ArchetypeConfig] = Field(..., description="Mapping of archetype name to its recipe configuration.")
    ingredients: IngredientsConfig = Field(..., description="Global ingredient settings.")

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("BiddingAgent")

# ---------------------------------------------------------------------------
# .env file path — resolved once relative to the repo root.
# The repo root is two levels up from this file:
#   hp2/agents/bidding_agent.py  →  ../../.env
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Budget fraction — we spend at most this share of our balance on bids.
BUDGET_FRACTION = 0.1

# Maximum portions per dish we ever want to stock.  The budget is spent
# proportionally across all ingredients; this cap prevents over-stocking
# when the balance is very large.
MAX_PORTIONS_PER_DISH = 5


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.json"


def _load_config(config_path: Path | None = None) -> BiddingConfig:
    """Load and validate the config file using Pydantic.

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
    path = config_path or Path(os.environ.get("BIDDING_AGENT_CONFIG", str(_DEFAULT_CONFIG_PATH)))
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at {path}.  "
            "Create it or set BIDDING_AGENT_CONFIG to the correct path."
        )

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    return BiddingConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Pure helper: compile the ingredient bid list from configuration
# ---------------------------------------------------------------------------


def _compile_bids(
    config: BiddingConfig,
    all_recipes: List[RecipeSchema],
    balance: float,
    budget_fraction: float = BUDGET_FRACTION,
    max_portions_per_dish: int = MAX_PORTIONS_PER_DISH,
) -> List[BidRequest]:
    """Build a deterministic list of ``BidRequest`` objects from configuration.

    Algorithm
    ---------
    1. **Collect all desired dish names** across every archetype in config.
    2. **Resolve each dish to its real ingredients** using the server
       recipe catalogue (``all_recipes``).
    3. **Aggregate ingredient demand** — each ingredient gets
       ``max_portions_per_dish`` units per dish occurrence (capped).
    4. **Use the global ``bidding_price``** as the bid price.
    5. **Budget-aware scaling** — scale down if needed.

    Parameters
    ----------
    config :
        Validated ``BiddingConfig`` model.
    all_recipes :
        Server recipe catalogue from ``get_recipes()``.
    balance :
        Current restaurant balance.
    budget_fraction :
        Share of balance to allocate for bids (default ``BUDGET_FRACTION``).
    max_portions_per_dish :
        Hard cap on portions per dish (default ``MAX_PORTIONS_PER_DISH``).

    Returns
    -------
    list[BidRequest]
        Ready to pass to ``client.submit_closed_bids()``.
        Empty list if config yields no ingredients.
    """
    bid_price: float = config.ingredients.bidding_price

    # Build a lookup: recipe_name → RecipeSchema
    recipe_lookup: Dict[str, RecipeSchema] = {r.name: r for r in all_recipes}

    # ── Step 1: Collect desired dishes (with demand count) ────────────
    dish_demand: Dict[str, float] = {}
    for _archetype_name, archetype_cfg in config.recipes.items():
        for dish_name in archetype_cfg.recipes:
            dish_demand[dish_name] = min(
                dish_demand.get(dish_name, 0.0) + 1, max_portions_per_dish
            )

<<<<<<< Updated upstream
        for entry in dish_list:
            dish_name = entry["name"]
            ingredient_list = arch_ingredients.get(dish_name, [])

            for ing in ingredient_list:
                ing_name = ing["name"]
                ing_price = float(ing.get("price", 1.0))

                if ing_name not in ingredient_info:
                    ingredient_info[ing_name] = {"bid": ing_price, "base_qty": 0.0}

                # Always keep the highest bid price across archetypes.
                if ing_price > ingredient_info[ing_name]["bid"]:
                    ingredient_info[ing_name]["bid"] = ing_price

                # Accumulate demand weighted by the archetype multiplier.
                ingredient_info[ing_name]["base_qty"] += MAX_PORTIONS_PER_DISH

    if not ingredient_info:
=======
    if not dish_demand:
>>>>>>> Stashed changes
        return []

    # ── Step 2: Resolve dishes → real ingredients ─────────────────────
    #
    # ingredient_demand maps ingredient_name → total quantity needed.
    # For each desired dish, look up its ingredients from the server
    # catalogue and accumulate demand.
    ingredient_demand: Dict[str, float] = {}
    for dish_name, portions in dish_demand.items():
        recipe = recipe_lookup.get(dish_name)
        if recipe is None:
            logger.warning(
                "Recipe '%s' from config not found in server catalogue — skipping.",
                dish_name,
            )
            continue
        for ing_name, ing_qty in recipe.ingredients.items():
            ingredient_demand[ing_name] = (
                ingredient_demand.get(ing_name, 0.0) + portions * ing_qty
            )

    if not ingredient_demand:
        return []

    # ── Step 3: Build raw bid list ────────────────────────────────────
    budget = balance * budget_fraction
<<<<<<< Updated upstream
    cost_per_round = sum(info["base_qty"] * info["bid"] for info in ingredient_info.values())

    if cost_per_round <= 0:
        return []
    
    logger.info(
        "Budget: %.0f ",
        budget,
    )

    # ── Step 4: Round quantities — no forced floor of 1 ───────────────────
    #
    # Forcing qty = max(1, ...) would distort proportions and silently blow
    # the budget (many cheap ingredients, each bumped to 1, add up fast).
    # Instead we let rounding produce 0 and filter those out below.
    bids_raw: List[Dict[str, Any]] = []
    for ing_name, info in ingredient_info.items():
        bid = max(1, round(info["bid"]))
        qty = info["base_qty"]
=======
    bids_raw: List[Dict[str, Any]] = []
    for ing_name, demand in ingredient_demand.items():
        bid = max(1, round(bid_price))
        qty = round(demand)
>>>>>>> Stashed changes
        if qty > 0:
            bids_raw.append({"ingredient": ing_name, "bid": bid, "quantity": qty})

    if not bids_raw:
        return []

    # ── Step 4: Post-rounding budget check ────────────────────────────
    actual_spend = sum(b["bid"] * b["quantity"] for b in bids_raw)
    if actual_spend > budget:
        scale = budget / actual_spend
        logger.info(
            "Post-rounding spend %.0f > budget %.0f — trimming by %.3f",
            actual_spend,
            budget,
            scale,
        )
        trimmed = []
        for b in bids_raw:
            qty = round(b["quantity"] * scale)
            if qty > 0:
                trimmed.append({**b, "quantity": qty})
        bids_raw = trimmed

    # ── Step 5: Spend leftover budget by topping up highest-demand items
    #
    # Cycle through ingredients (sorted by demand weight desc) repeatedly,
    # adding +1 each pass, until the budget is exhausted.  This distributes
    # surplus budget proportionally toward high-demand ingredients.
    spend = sum(b["bid"] * b["quantity"] for b in bids_raw)
    remaining = budget - spend

    bid_map: Dict[str, Dict[str, Any]] = {b["ingredient"]: b for b in bids_raw}

    sorted_by_demand = sorted(
        bids_raw,
        key=lambda b: ingredient_demand.get(b["ingredient"], 0.0),
        reverse=True,
    )

    min_bid = min(b["bid"] for b in bids_raw) if bids_raw else 1
    topped = True
    while topped and remaining >= min_bid:
        topped = False
        for b in sorted_by_demand:
            if remaining < b["bid"]:
                continue  # skip this ingredient, try cheaper ones
            bid_map[b["ingredient"]]["quantity"] += 1
            remaining -= b["bid"]
            topped = True

    final_spend = sum(b["bid"] * b["quantity"] for b in bids_raw)
    if final_spend > spend:
        logger.info("Topped up ingredients with %.0f leftover budget.", final_spend - spend)

    return [
        BidRequest(ingredient=b["ingredient"], bid=b["bid"], quantity=b["quantity"])
        for b in bids_raw
    ]


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class BiddingAgent(BaseAgent):
    """Deterministic agent that handles only the **closed_bid** phase.

    When the game phase transitions to ``closed_bid``, this agent:

    1. Reloads ``config.json`` (allows hot-editing between turns).
    2. Fetches game state (balance from the server).
    3. Computes bids deterministically from configuration prices and quantities.
    4. Calls ``client.submit_closed_bids(bids)`` — a standalone MCP tool call,
       **not** an agentic LLM loop.

    All other phase events are logged and ignored.

    Parameters
    ----------
    client : HackapizzaClient or None
        Live client.  Ignored when ``test_mode=True``.
    config_path : Path or None
        Override for ``config.json`` location.
    """

    def __init__(
        self,
        client: HackapizzaClient | None = None,
        config_path: Path | None = None,
        *,
        test_mode: bool = False,
    ):
        self.test_mode = test_mode
        self.logger = logging.getLogger("BiddingAgent")

        self._config_path = config_path
        super().__init__(client)

        # Step 4: Pre-load the configuration.
        self._config: BiddingConfig = _load_config(config_path)
        self.logger.info(
            "Loaded config with %d archetype(s) and %d total dish entries.",
            len(self._config.recipes),
            sum(len(a.recipes) for a in self._config.recipes.values()),
        )

        # NOTE: No LLM agent, MCP client, or OpenAI client is created here.
        # All bidding logic is deterministic — computed from configuration
        # and game state using pure functions.

    # ------------------------------------------------------------------
    # SSE event handlers
    # ------------------------------------------------------------------

    async def on_game_started(self, event: GameStartedEvent) -> None:
        self.logger.info("Game started — turn_id: %s", event.turn_id)

    async def on_phase_changed(self, phase: GamePhase) -> None:
        self.logger.info("Phase changed to: %s", phase.value)

        if phase == GamePhase.CLOSED_BID:
            await self.phase_closed_bid()
        else:
            self.logger.debug(
                "Phase '%s' is not handled by BiddingAgent — ignoring.",
                phase.value,
            )

    async def on_client_spawned(self, order: ClientOrder) -> None:
        pass

    async def on_preparation_complete(self, dish_name: str) -> None:
        self.logger.debug("Preparation complete (ignored): %s", dish_name)

    async def on_new_message(self, message: IncomingMessage) -> None:
        self.logger.debug(
            "Message from %s (ignored): %s",
            message.sender_name,
            message.text[:80],
        )

    # ------------------------------------------------------------------
    # Core closed-bid logic (deterministic, no LLM)
    # ------------------------------------------------------------------

    async def phase_closed_bid(self) -> List[BidRequest]:
        """Execute the deterministic closed-bid workflow.

        Steps
        -----
        1. **Reload config** — ``config.json`` is re-read every turn
           so you can hot-edit prices / recipes between rounds.
        2. **Fetch balance** — ``get_my_restaurant()`` gives us the current
           balance which determines the budget cap.
        3. **Compile bids** — ``_compile_bids()`` deterministically builds
           the ingredient list, quantities, and bid prices from config.
        4. **Submit bids** — ``client.submit_closed_bids(bids)`` sends a
           single MCP ``closed_bid`` call.  This is a standalone function
           call, NOT an agentic tool use.

        Returns
        -------
        list[BidRequest]
            The bids that were submitted (or would have been, if empty).
        """
        self.logger.info("=== CLOSED BID PHASE START ===")

        # ── 1. Reload config (pick up hot changes) ────────────────────
        #    In test mode we skip reloading — we use MOCK_CONFIGURATION.
        if not self.test_mode:
            try:
                self._config = _load_config(self._config_path)
            except Exception as exc:
                self.logger.warning("Could not reload config (%s); using cached version.", exc)

        # ── 2. Fetch game state and recipe catalogue ──────────────────
        balance = 0.0
        all_recipes: List[RecipeSchema] = []
        try:
            restaurant = await self.client.get_my_restaurant()
            balance = restaurant.balance
        except Exception as exc:
            self.logger.error("Failed to fetch restaurant state: %s", exc)

        try:
            all_recipes = await self.client.get_recipes()
            self.logger.info("Fetched %d recipe(s) from the server.", len(all_recipes))
        except Exception as exc:
            self.logger.error("Failed to fetch recipes: %s", exc)

        self.logger.info("Current balance: %.2f", balance)

        # ── 3. Compute bids deterministically from config ─────────────
        bids: List[BidRequest] = _compile_bids(
            config=self._config,
            all_recipes=all_recipes,
            balance=balance,
        )

        if not bids:
            self.logger.warning("No bids compiled from configuration — nothing to submit.")
            self.logger.info("=== CLOSED BID PHASE COMPLETE ===")
            return []

        # Log what we're about to submit for transparency / debugging.
        total_spend = sum(b.bid * b.quantity for b in bids)
        self.logger.info(
            "Compiled %d bid(s) — projected spend: %.0f (budget cap: %.0f)",
            len(bids),
            total_spend,
            balance * BUDGET_FRACTION,
        )
        for b in bids:
            self.logger.debug(
                "  %-40s  bid=%d  qty=%d  subtotal=%d",
                b.ingredient,
                b.bid,
                b.quantity,
                b.bid * b.quantity,
            )

        # ── 4. Submit bids via the closed_bid MCP tool ────────────────
        #
        #   submit_closed_bids(bids) sends a JSON-RPC POST to /mcp.
        #   bids = [BidRequest(ingredient=str, bid=float, quantity=int), ...]
        #   This is a standalone function call, NOT an agentic tool use.
        try:
            result = await self.client.submit_closed_bids(bids)
            self.logger.info(
                "submit_closed_bids succeeded — %d bid(s) sent. Response: %s",
                len(bids),
                result,
            )
        except Exception as exc:
            self.logger.error("submit_closed_bids FAILED: %s", exc)

        self.logger.info("=== CLOSED BID PHASE COMPLETE ===")
        return bids

    # ------------------------------------------------------------------
    # Entry-point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the agent.

        In test mode runs ``phase_closed_bid()`` once and returns.
        In live mode connects to the SSE stream.
        """
        if self.test_mode:
            self.logger.info("[TEST MODE] Running phase_closed_bid() once…")
            bids = await self.phase_closed_bid()
            self.logger.info(
                "[TEST MODE] Done. Submitted %d bid(s): %s",
                len(bids),
                [(b.ingredient, b.bid, b.quantity) for b in bids],
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
        description="BiddingAgent — deterministic closed-bid submitter.",
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

    agent = BiddingAgent(config_path=args.config, test_mode=args.test)
    asyncio.run(agent.run())
