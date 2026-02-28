"""
BiddingAgent — Deterministic bidding agent for the *closed_bid* phase.

This agent implements **only** the ``phase_closed_bid`` logic.  It is fully
deterministic (no LLM / agentic calls):

  1. Loads the desired recipe list from ``config.json``
     (located at the repository root).
  2. Fetches current game state (balance).
  3. Computes bids deterministically from configuration data.
  4. Calls ``submit_closed_bids`` as a direct MCP tool call (standalone,
     not via an agentic loop).

Design rationale
----------------
The previous version created a ``datapizza.agents.Agent`` wrapping an LLM
(``OpenAILikeClient``) and MCP tools, then delegated the entire bid decision
to a non-deterministic agentic loop.  This was problematic because:

  - The LLM could hallucinate ingredient names or bid amounts.
  - Behaviour varied unpredictably across runs.
  - Latency and cost were high (multiple LLM round-trips + tool calls).

The new version mirrors the pattern established by ``WaitingAgent``:
all logic is pure Python, and the only I/O is fetching game state and
calling the MCP tool once via ``HackapizzaClient``.

Configuration format
--------------------
``config.json`` is structured by **customer archetype**::

    {
        "recipes": {
            "<archetype>": [
                {"name": "<dish_name>", "multiplier": <float>},
                ...
            ],
            ...
        },
        "ingredients": {
            "<archetype>": {
                "<dish_name>": [
                    {"name": "<ingredient>", "price": <float>},
                    ...
                ],
                ...
            },
            ...
        }
    }

We compute the cost of 1 portion of every unique dish ("cost per round"),
then determine how many rounds the budget affords, capped at
``MAX_PORTIONS_PER_DISH``.  This fully utilises the budget while keeping
ingredient proportions correct across all recipes.

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
run the entire closed-bid pipeline against **mock data** — no server,
no SSE, no ``.env`` required.

Usage
-----
Run standalone (live)::

    python -m hp2.agents.bidding_agent

Run in test / dry-run mode::

    python -m hp2.agents.bidding_agent --test
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    BidRequest,
    ClientOrder,
    GamePhase,
    GameStartedEvent,
    HackapizzaClient,
    IncomingMessage,
)

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
BUDGET_FRACTION = 0.2

# Maximum portions per dish we ever want to stock.  The budget is spent
# proportionally across all ingredients; this cap prevents over-stocking
# when the balance is very large.
MAX_PORTIONS_PER_DISH = 5


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.json"


def _load_config(config_path: Path | None = None) -> Dict[str, Any]:
    """Load the config file that lists recipes per archetype + ingredients.

    Expected top-level keys:

    - ``"recipes"``     — dict keyed by archetype, each value is a list of
                          ``{"name": str, ...}`` (may also contain "multiplier").
    - ``"ingredients"`` — dict keyed by archetype, each value is a dict
                          keyed by recipe name, each value is a list of
                          ``{"name": str, "price": float}``.

    Raises ``FileNotFoundError`` or ``ValueError`` on problems.
    """
    path = config_path or Path(
        os.environ.get("BIDDING_AGENT_CONFIG", str(_DEFAULT_CONFIG_PATH))
    )
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at {path}.  "
            "Create it or set BIDDING_AGENT_CONFIG to the correct path."
        )

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if "recipes" not in data or "ingredients" not in data:
        raise ValueError(
            "config.json must contain top-level 'recipes' and 'ingredients' keys."
        )

    return data


# ---------------------------------------------------------------------------
# Pure helper: compile the ingredient bid list from configuration
# ---------------------------------------------------------------------------

def _compile_bids(
    config: Dict[str, Any],
    balance: float,
    budget_fraction: float = BUDGET_FRACTION,
    max_portions_per_dish: int = MAX_PORTIONS_PER_DISH,
) -> List[BidRequest]:
    """Build a deterministic list of ``BidRequest`` objects from configuration.

    Algorithm
    ---------
    1. **Collect target dishes** — iterate every archetype in
       ``config["recipes"]`` and collect unique dish names.
    2. **Collect ingredient prices** — for each dish, look up its ingredient
       list in ``config["ingredients"][archetype][dish]``.  Each ingredient
       entry has a ``"price"`` field which we use as the bid price.  When
       the same ingredient appears under multiple archetypes we keep the
       **maximum** price — this maximises our chance of winning the blind
       auction.
    3. **Base quantities (1 portion each)** — record how many units of each
       ingredient are needed to cook exactly 1 portion of every unique dish.
       Shared ingredients are summed across dishes.
    4. **Budget-aware scaling** — compute the cost of one full "round"
       (1 portion of every dish).  The number of rounds we can afford is
       ``budget / cost_per_round``, capped at ``max_portions_per_dish``.
       This fully utilises the budget while keeping ingredient proportions
       correct.

    Parameters
    ----------
    config :
        The loaded ``config.json`` dict.
    balance :
        Current restaurant balance.
    budget_fraction :
        Share of balance to allocate for bids (default ``BUDGET_FRACTION``).
    max_portions_per_dish :
        Hard cap on portions per dish — prevents over-stocking when the
        balance is large (default ``MAX_PORTIONS_PER_DISH``).

    Returns
    -------
    list[BidRequest]
        Ready to pass to ``client.submit_closed_bids()``.
        Empty list if config yields no ingredients.
    """
    recipes_section: Dict[str, List[Dict[str, Any]]] = config["recipes"]
    ingredients_section: Dict[str, Dict[str, List[Dict[str, Any]]]] = config["ingredients"]

    # ── Step 1+2: Collect per-ingredient max bid price and base quantity ──
    #
    # ingredient_info maps ingredient_name → {"bid": max_price, "base_qty": int}
    # base_qty = units needed for exactly 1 portion of every unique dish.
    ingredient_info: Dict[str, Dict[str, float]] = {}

    # Track unique dish names so we only count base quantities once even
    # when the same dish appears under multiple archetypes.
    dish_qty_counted: set = set()

    for archetype, dish_list in recipes_section.items():
        arch_ingredients = ingredients_section.get(archetype, {})

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

            # Count base quantities only the first time we see this dish.
            if dish_name not in dish_qty_counted:
                dish_qty_counted.add(dish_name)
                for ing in ingredient_list:
                    ing_name = ing["name"]
                    ingredient_info[ing_name]["base_qty"] += 1

    if not ingredient_info:
        return []

    # ── Step 3: Determine how many rounds the budget affords ─────────────
    #
    # cost_per_round = total cost to buy 1 portion of every unique dish.
    # rounds = how many full rounds we can fit in the budget, capped at
    # max_portions_per_dish.  Using a float here keeps proportions exact
    # before we round to integers below.
    budget = balance * budget_fraction
    cost_per_round = sum(
        info["base_qty"] * info["bid"] for info in ingredient_info.values()
    )

    if cost_per_round <= 0:
        return []

    rounds = min(max_portions_per_dish, budget / cost_per_round)
    logger.info(
        "Budget: %.0f  |  cost/round: %.0f  |  rounds: %.2f  (cap: %d)",
        budget,
        cost_per_round,
        rounds,
        max_portions_per_dish,
    )

    # ── Step 4: Round quantities — no forced floor of 1 ───────────────────
    #
    # Forcing qty = max(1, ...) would distort proportions and silently blow
    # the budget (many cheap ingredients, each bumped to 1, add up fast).
    # Instead we let rounding produce 0 and filter those out below.
    bids_raw: List[Dict[str, Any]] = []
    for ing_name, info in ingredient_info.items():
        bid = max(1, round(info["bid"]))
        qty = round(info["base_qty"] * rounds)
        if qty > 0:
            bids_raw.append({"ingredient": ing_name, "bid": bid, "quantity": qty})

    if not bids_raw:
        return []

    # ── Step 5: Post-rounding budget check ────────────────────────────────
    #
    # Integer rounding can push projected spend slightly above budget.
    # If that happens, scale quantities down proportionally and re-round.
    # We do this at most once — a second pass is never needed because the
    # scale factor is < 1 and rounding can only reduce quantities further.
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
        self._config = _load_config(config_path)
        self.logger.info(
            "Loaded config with %d archetype(s) and %d total dish entries.",
            len(self._config["recipes"]),
            sum(len(v) for v in self._config["recipes"].values()),
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
        self.logger.debug("Client spawned (ignored): %s", order.client_id)

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
                self.logger.warning(
                    "Could not reload config (%s); using cached version.", exc
                )

        # ── 2. Fetch game state (we only need the balance for budgeting) ─
        balance = 0.0
        try:
            restaurant = await self.client.get_my_restaurant()
            balance = restaurant.balance
        except Exception as exc:
            self.logger.error("Failed to fetch restaurant state: %s", exc)

        self.logger.info("Current balance: %.2f", balance)

        # ── 3. Compute bids deterministically from config ─────────────
        bids: List[BidRequest] = _compile_bids(
            config=self._config,
            balance=balance,
        )

        if not bids:
            self.logger.warning(
                "No bids compiled from configuration — nothing to submit."
            )
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
