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

For each unique dish across all archetypes, we bid for 2 portions of every
ingredient, using the **maximum** price seen across archetypes as the bid
price.  A budget cap of 60 % of the current balance is enforced.

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
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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
_DOTENV_PATH = _REPO_ROOT / ".env"


def _load_dotenv() -> None:
    """Load the ``.env`` file from the repository root into ``os.environ``.

    This ensures that ``pydantic-settings`` (used by ``get_settings()`` and
    ``get_sql_logging_settings()``) can pick up the variables regardless of
    the current working directory.

    Uses ``python-dotenv`` which is already an indirect dependency of
    ``pydantic-settings``.  Existing env vars are **not** overwritten.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.debug(
            "python-dotenv not installed; relying on pydantic-settings "
            "env_file handling.  Make sure CWD is the repo root."
        )
        return

    if _DOTENV_PATH.is_file():
        load_dotenv(_DOTENV_PATH, override=False)
        logger.info("Loaded .env from %s", _DOTENV_PATH)
    else:
        logger.warning(
            ".env file not found at %s — environment variables must "
            "already be set in the shell.",
            _DOTENV_PATH,
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Budget fraction — we spend at most this share of our balance on bids.
# Matches the 60 % cap that the old LLM prompt enforced.
BUDGET_FRACTION = 0.60

# How many portions of each target dish we want to be able to cook.
# The old prompt asked the LLM to "bid for enough ingredients to cook
# 2 portions of each target dish".
PORTIONS_PER_DISH = 2


# ---------------------------------------------------------------------------
# Mock data — used exclusively in test / dry-run mode
# ---------------------------------------------------------------------------

# Mock restaurant state: balance of 1000, some existing inventory.
# The bidding agent only needs the balance; inventory is informational.
MOCK_RESTAURANT_PAYLOAD: Dict[str, Any] = {
    "id": "6",
    "name": "RAGù",
    "balance": 1000.0,
    "inventory": {
        "Lattuga Namecciana": 2,
        "Carne di Balena spaziale": 3,
        "Fusilli del Vento": 1,
        "Pane di Luce": 1,
        "Lacrime di Unicorno": 1,
        "Essenza di Speziaria": 1,
        "Carne di Mucca": 2,
        "Carne di Xenodonte": 2,
        "Pane degli Abissi": 2,
        "Funghi dell'Etere": 1,
        "Shard di Prisma Stellare": 1,
        "Teste di Idra": 1,
        "Essenza di Vuoto": 1,
    },
    "reputation": 100.0,
    "isOpen": True,
    "kitchen": [],
    "menu": {"items": []},
    "receivedMessages": [],
}

# Mock configuration matching the real config.json structure.
# Three unique dishes across four archetypes.
#
# Expected bid computation (PORTIONS_PER_DISH = 2):
#   Dish 1 — "Luce e Ombra di Nomea Spaziale" (Esploratore Galattico):
#     6 ingredients × price 5 each → bids at 5, qty 2 each
#   Dish 2 — "Sinfonia Cosmica di Proteine Interstellari"
#     (Famiglie Orbitali @ 6 AND Astrobarone @ 10):
#     5 ingredients → max price = 10, qty 2 each
#   Dish 3 — "Sinfonia di Multiverso: La Danza degli Elementi"
#     (Saggi del Cosmo @ 8):
#     5 ingredients → bids at 8, qty 2 each
#
# Overlapping ingredient: "Carne di Balena spaziale" appears in all 3 dishes:
#   prices: 5 (dish 1), 10 (dish 2 via Astrobarone), 8 (dish 3)
#   → max bid = 10, quantity = 2 + 2 + 2 = 6
#
# Total projected spend should be well within 60 % of 1000 = 600.
MOCK_CONFIGURATION: Dict[str, Any] = {
    "recipes": {
        "Esploratore Galattico": [
            {"name": "Luce e Ombra di Nomea Spaziale", "multiplier": 1.0},
        ],
        "Famiglie Orbitali": [
            {"name": "Sinfonia Cosmica di Proteine Interstellari", "multiplier": 1.2},
        ],
        "Saggi del Cosmo": [
            {"name": "Sinfonia di Multiverso: La Danza degli Elementi", "multiplier": 1.5},
        ],
        "Astrobarone": [
            {"name": "Sinfonia Cosmica di Proteine Interstellari", "multiplier": 2.0},
        ],
    },
    "ingredients": {
        "Esploratore Galattico": {
            "Luce e Ombra di Nomea Spaziale": [
                {"name": "Lattuga Namecciana", "price": 5.0},
                {"name": "Carne di Balena spaziale", "price": 5.0},
                {"name": "Fusilli del Vento", "price": 5.0},
                {"name": "Pane di Luce", "price": 5.0},
                {"name": "Lacrime di Unicorno", "price": 5.0},
                {"name": "Essenza di Speziaria", "price": 5.0},
            ],
        },
        "Famiglie Orbitali": {
            "Sinfonia Cosmica di Proteine Interstellari": [
                {"name": "Carne di Balena spaziale", "price": 6.0},
                {"name": "Carne di Mucca", "price": 6.0},
                {"name": "Carne di Xenodonte", "price": 6.0},
                {"name": "Pane degli Abissi", "price": 6.0},
                {"name": "Funghi dell'Etere", "price": 6.0},
            ],
        },
        "Saggi del Cosmo": {
            "Sinfonia di Multiverso: La Danza degli Elementi": [
                {"name": "Shard di Prisma Stellare", "price": 8.0},
                {"name": "Carne di Balena spaziale", "price": 8.0},
                {"name": "Carne di Drago", "price": 8.0},
                {"name": "Teste di Idra", "price": 8.0},
                {"name": "Essenza di Vuoto", "price": 8.0},
            ],
        },
        "Astrobarone": {
            "Sinfonia Cosmica di Proteine Interstellari": [
                {"name": "Carne di Balena spaziale", "price": 10.0},
                {"name": "Carne di Mucca", "price": 10.0},
                {"name": "Carne di Xenodonte", "price": 10.0},
                {"name": "Pane degli Abissi", "price": 10.0},
                {"name": "Funghi dell'Etere", "price": 10.0},
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Mock client — stands in for HackapizzaClient during test mode
# ---------------------------------------------------------------------------

class _MockHackapizzaClient:
    """Minimal stand-in for ``HackapizzaClient`` that returns mock data.

    Only the methods required by ``phase_closed_bid`` are implemented.
    ``submit_closed_bids`` records what it receives so tests can assert on it.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("MockHackapizzaClient")
        # Every call to submit_closed_bids appends its argument here,
        # making it easy for tests to inspect what was submitted.
        self.submit_closed_bids_calls: List[List[BidRequest]] = []

    async def get_my_restaurant(self):
        """Return a mock RestaurantSchema with a known balance."""
        from hp2.core.schema.models import RestaurantSchema
        return RestaurantSchema.model_validate(MOCK_RESTAURANT_PAYLOAD)

    async def submit_closed_bids(self, bids: List[BidRequest]) -> Dict[str, Any]:
        """Record the bids and return a mock success response."""
        self.submit_closed_bids_calls.append(bids)
        self.logger.info(
            "[MOCK] submit_closed_bids called with %d bid(s): %s",
            len(bids),
            [(b.ingredient, b.bid, b.quantity) for b in bids],
        )
        return {"content": [{"text": "Bids placed successfully"}], "isError": False}

    async def start(self):
        """No-op in test mode."""
        self.logger.info("[MOCK] start() called — no-op in test mode.")


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
    portions: int = PORTIONS_PER_DISH,
) -> List[BidRequest]:
    """Build a deterministic list of ``BidRequest`` objects from configuration.

    Algorithm (mirrors the instructions that were previously given to the LLM):

    1. **Collect target dishes** — iterate every archetype in
       ``config["recipes"]`` and collect unique dish names.
    2. **Collect ingredient prices** — for each dish, look up its ingredient
       list in ``config["ingredients"][archetype][dish]``.  Each ingredient
       entry has a ``"price"`` field which we use as the bid price.  When
       the same ingredient appears under multiple archetypes (because the
       same dish is listed in several archetypes, or different dishes share
       an ingredient), we keep the **maximum** price — this maximises our
       chance of winning the blind auction.
    3. **Aggregate quantities** — each dish needs ``portions`` copies of
       every ingredient.  If two dishes share an ingredient, quantities are
       summed.
    4. **Budget cap** — if the total projected spend exceeds
       ``balance × budget_fraction``, we scale all quantities down
       proportionally (rounding up to keep at least 1 of each).

    Parameters
    ----------
    config :
        The loaded ``config.json`` dict.
    balance :
        Current restaurant balance.
    budget_fraction :
        Maximum share of balance to spend (default 0.60).
    portions :
        Number of portions per dish to bid for (default 2).

    Returns
    -------
    list[BidRequest]
        Ready to pass to ``client.submit_closed_bids()``.
        Empty list if config yields no ingredients.
    """
    recipes_section: Dict[str, List[Dict[str, Any]]] = config["recipes"]
    ingredients_section: Dict[str, Dict[str, List[Dict[str, Any]]]] = config["ingredients"]

    # ── Step 1+2: Collect per-ingredient max bid price and total quantity ──
    #
    # ingredient_info maps ingredient_name → {"bid": max_price, "quantity": total_qty}
    ingredient_info: Dict[str, Dict[str, float]] = {}

    # Track which unique dish names we've already counted quantities for.
    # When the same dish appears under multiple archetypes (e.g.
    # "Sinfonia Cosmica…" under both Famiglie Orbitali and Astrobarone),
    # we only count its portions once — we want 2 portions total, not 2
    # per archetype.  However we still scan every archetype to find the
    # maximum bid price.
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
                    # First time seeing this ingredient — initialise.
                    ingredient_info[ing_name] = {"bid": ing_price, "quantity": 0.0}

                # Always keep the highest bid price across archetypes.
                if ing_price > ingredient_info[ing_name]["bid"]:
                    ingredient_info[ing_name]["bid"] = ing_price

            # Add quantities only the first time we encounter this dish.
            if dish_name not in dish_qty_counted:
                dish_qty_counted.add(dish_name)
                for ing in ingredient_list:
                    ing_name = ing["name"]
                    # Each ingredient is needed once per portion per dish.
                    ingredient_info[ing_name]["quantity"] += portions

    if not ingredient_info:
        return []

    # ── Step 3: Convert to integer bids and quantities ────────────────────
    #
    # The closed_bid MCP tool requires bid and quantity to be integers > 0.
    bids_raw: List[Dict[str, Any]] = []
    for ing_name, info in ingredient_info.items():
        bid = max(1, round(info["bid"]))       # integer bid, minimum 1
        qty = max(1, round(info["quantity"]))   # integer quantity, minimum 1
        bids_raw.append({"ingredient": ing_name, "bid": bid, "quantity": qty})

    # ── Step 4: Budget cap — scale down if projected spend exceeds budget ─
    budget = balance * budget_fraction
    projected_spend = sum(b["bid"] * b["quantity"] for b in bids_raw)

    if projected_spend > budget and projected_spend > 0:
        # Scale factor < 1.0 — reduce quantities proportionally.
        scale = budget / projected_spend
        logger.info(
            "Projected spend %.0f exceeds budget %.0f — scaling quantities by %.2f",
            projected_spend,
            budget,
            scale,
        )
        for b in bids_raw:
            # math.ceil ensures we don't round to 0; max(1, ...) is a safety net.
            b["quantity"] = max(1, math.ceil(b["quantity"] * scale))

    # ── Step 5: Build typed BidRequest objects ────────────────────────────
    return [
        BidRequest(
            ingredient=b["ingredient"],
            bid=b["bid"],
            quantity=b["quantity"],
        )
        for b in bids_raw
        if b["quantity"] > 0
    ]


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

class BiddingAgent:
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
        self.logger = logging.getLogger("BiddingAgent")

        if test_mode:
            # ── TEST MODE: mock client, mock config, no .env needed ────
            self.client: Any = _MockHackapizzaClient()
            self._config = MOCK_CONFIGURATION
            self._config_path: Optional[Path] = None
            self.logger.info(
                "[TEST MODE] Initialised with mock client and %d archetype(s).",
                len(self._config["recipes"]),
            )
        else:
            # ── LIVE MODE ──────────────────────────────────────────────
            # Step 1: Load .env from repo root.
            _load_dotenv()

            # Step 2: Read settings via pydantic-settings (backed by .env).
            from hp2.core.settings import get_settings, get_sql_logging_settings

            settings = get_settings()
            sql_settings = get_sql_logging_settings()

            self.logger.info(
                "Settings loaded — team_id=%d, api_key set=%s, sql_connstr set=%s",
                settings.hackapizza_team_id,
                bool(settings.hackapizza_team_api_key),
                bool(sql_settings.hackapizza_sql_connstr),
            )

            # Step 3: Build or re-use the HackapizzaClient.
            self.client = client or HackapizzaClient(
                team_id=settings.hackapizza_team_id,
                api_key=settings.hackapizza_team_api_key,
                enable_sql_logging=True,
                sql_connstr=sql_settings.hackapizza_sql_connstr,
            )

            # Step 4: Pre-load the configuration.
            self._config_path = config_path
            self._config = _load_config(config_path)
            self.logger.info(
                "Loaded config with %d archetype(s) and %d total dish entries.",
                len(self._config["recipes"]),
                sum(len(v) for v in self._config["recipes"].values()),
            )

            # Step 5: Register SSE event handlers.
            self._register_event_handlers()

        # NOTE: No LLM agent, MCP client, or OpenAI client is created here.
        # All bidding logic is deterministic — computed from configuration
        # and game state using pure functions.

    # ------------------------------------------------------------------
    # SSE event handler registration (live mode only)
    # ------------------------------------------------------------------

    def _register_event_handlers(self) -> None:
        """Wire SSE callbacks to our handler methods."""

        @self.client.on_game_started
        async def _on_game_started(event: GameStartedEvent) -> None:
            await self.on_game_started(event)

        @self.client.on_phase_changed
        async def _on_phase_changed(phase: GamePhase) -> None:
            await self.on_phase_changed(phase)

        @self.client.on_client_spawned
        async def _on_client_spawned(order: ClientOrder) -> None:
            await self.on_client_spawned(order)

        @self.client.on_preparation_complete
        async def _on_preparation_complete(dish_name: str) -> None:
            await self.on_preparation_complete(dish_name)

        @self.client.on_new_message
        async def _on_new_message(message: IncomingMessage) -> None:
            await self.on_new_message(message)

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
            self.logger.info("Starting agent %s…", self.__class__.__name__)
            await self.client.start()


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

    if not args.test:
        _load_dotenv()

    agent = BiddingAgent(config_path=args.config, test_mode=args.test)
    asyncio.run(agent.run())
