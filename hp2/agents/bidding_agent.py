"""
BiddingAgent — LLM-powered bidding agent for the *closed_bid* phase.

This agent extends ``BaseAgent`` and implements **only** the ``phase_closed_bid``
logic.  It uses an LLM (via datapizza Agent + MCP tools) to decide bids:

  1. Loads the desired recipe list from ``configuration.json``
     (located at the repository root).
  2. Fetches current game state (balance, inventory, recipes, market).
  3. Sends all context to the LLM and lets it call ``closed_bid`` via MCP.

Usage
-----
Run standalone::

    python -m hp2.agents.bidding

Or import and wire into a broader orchestrator that calls
``await agent.phase_closed_bid()`` when the phase changes to ``closed_bid``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from datapizza.agents import Agent
from datapizza.clients.openai_like import OpenAILikeClient
from datapizza.tools.mcp_client import MCPClient

from hp2.agents.base import BaseAgent
from hp2.core.api import (
    ClientOrder,
    GamePhase,
    HackapizzaClient,
    IncomingMessage,
)
from hp2.core.settings import get_settings

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("BiddingAgent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HACKAPIZZA_BASE_URL = "https://hackapizza.datapizza.tech"
MCP_ENDPOINT = f"{HACKAPIZZA_BASE_URL}/mcp"

BIDDING_SYSTEM_PROMPT = """\
You are the bidding strategist for restaurant "RAGù" (team 6) in Hackapizza 2.0.
Your ONLY job is to place smart blind-auction bids for ingredients using the closed_bid tool.

KEY RULES:
• Ingredients expire at the end of every turn — only buy what you will use.
• In the blind auction the highest bidder gets priority; you may receive less than requested.
• Do NOT exceed the budget indicated in the prompt.
• Bid for enough ingredients to cook 2 portions of each target dish.
• Prefer overlapping ingredients across dishes to minimise total spend.
• Call closed_bid ONCE with all bids together.

Always act decisively. Explain your reasoning briefly, then call the closed_bid tool."""

# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configuration.json"


def _load_config(config_path: Path | None = None) -> Dict[str, Any]:
    """Load the config file that lists recipes per archetype + ingredients."""
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
            "configuration.json must contain top-level 'recipes' and 'ingredients' keys."
        )

    return data


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

class BiddingAgent(BaseAgent):
    """LLM-powered agent that handles only the **closed_bid** phase.

    When the game phase transitions to ``closed_bid``, this agent:

    1. Loads ``configuration.json`` (recipes per archetype + ingredient lists).
    2. Fetches game state (balance, recipes, market entries).
    3. Sends everything to the LLM which calls ``closed_bid`` via MCP tools.

    All other phase events are logged and ignored.
    """

    def __init__(
        self,
        client: HackapizzaClient | None = None,
        config_path: Path | None = None,
    ):
        super().__init__(client)

        self.logger = logging.getLogger("BiddingAgent")

        self._config_path = config_path
        self._config = _load_config(config_path)
        self.logger.info(
            "Loaded config with %d archetype(s) and %d total dish entries.",
            len(self._config["recipes"]),
            sum(len(v) for v in self._config["recipes"].values()),
        )

        # ── LLM agent setup ──────────────────────────────────────────
        settings = get_settings()
        mcp_client = MCPClient(
            url=MCP_ENDPOINT,
            headers={"x-api-key": settings.hackapizza_team_api_key},
            timeout=30,
        )
        mcp_tools = mcp_client.list_tools()
        self.logger.info(
            "Loaded %d MCP tools: %s",
            len(mcp_tools),
            [t.name for t in mcp_tools],
        )

        regolo_client = OpenAILikeClient(
            api_key=settings.regolo_api_key,
            model="gpt-oss-120b",
            system_prompt=BIDDING_SYSTEM_PROMPT,
            base_url="https://api.regolo.ai/v1",
        )

        self._llm_agent = Agent(
            name="bidding_agent",
            client=regolo_client,
            tools=mcp_tools,
            max_steps=10,
        )

    # ------------------------------------------------------------------
    # SSE event handlers (BaseAgent contract)
    # ------------------------------------------------------------------

    async def on_game_started(self, data: Dict[str, Any]) -> None:
        self.logger.info("Game started — data: %s", data)

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
    # Core closed-bid logic (LLM-driven)
    # ------------------------------------------------------------------

    async def phase_closed_bid(self) -> None:
        """Use the LLM to decide and submit bids via the closed_bid MCP tool.

        Steps
        -----
        1. Reload ``configuration.json`` (allows hot-editing between turns).
        2. Fetch current game state from the server.
        3. Build a prompt with all context and let the LLM call closed_bid.
        """
        self.logger.info("=== CLOSED BID PHASE START ===")

        # ── 1. Reload config (pick up hot changes) ────────────────────
        try:
            self._config = _load_config(self._config_path)
        except Exception as exc:
            self.logger.warning(
                "Could not reload config (%s); using cached version.", exc
            )

        # ── 2. Fetch game state ──────────────────────────────────────
        balance = 0.0
        inventory: Dict[str, Any] = {}
        recipes_data: list = []
        market_data: list = []

        try:
            restaurant = await self.client.get_my_restaurant()
            balance = restaurant.balance
            inventory = restaurant.inventory
        except Exception as exc:
            self.logger.error("Failed to fetch restaurant state: %s", exc)

        try:
            recipes_data = [
                r.model_dump(by_alias=True) for r in await self.client.get_recipes()
            ]
        except Exception as exc:
            self.logger.error("Failed to fetch recipes: %s", exc)

        try:
            market_data = [
                m.model_dump(by_alias=True) for m in await self.client.get_market_entries()
            ]
        except Exception as exc:
            self.logger.error("Failed to fetch market entries: %s", exc)

        # ── 3. Build prompt and run LLM ──────────────────────────────
        budget = balance * 0.6
        prompt = f"""\
PHASE: CLOSED BID — Blind Auction

== Your balance ==
{balance}

== Current inventory ==
{json.dumps(inventory, indent=2, default=str)}

== Target recipes from config (per archetype) ==
{json.dumps(self._config["recipes"], indent=2)}

== Ingredient details from config (per archetype → dish → ingredients with bid prices) ==
{json.dumps(self._config["ingredients"], indent=2)}

== All available recipes from server ==
{json.dumps(recipes_data, indent=2, default=str)}

== P2P market entries ==
{json.dumps(market_data, indent=2, default=str)}

YOUR TASK:
1. From the target recipes in config, compile the full ingredient list needed
   to cook 2 portions of each unique dish.
2. Use the ingredient prices from config as your bid prices (they are already
   calibrated per archetype). Use the MAXIMUM price across archetypes for each
   ingredient.
3. Call closed_bid ONCE with ALL bids:
      closed_bid(bids=[{{"ingredient": "Name", "bid": <int>, "quantity": <int>}}, ...])
   - bid and quantity must be integers > 0.

BUDGET: Do NOT spend more than {budget:.0f} (60% of balance {balance}).
"""
        self.logger.info(
            "Sending bidding prompt to LLM (balance=%.0f, budget=%.0f)",
            balance,
            budget,
        )

        try:
            result = await self._llm_agent.a_run(prompt)
            self.logger.info(
                "LLM bidding agent finished — response: %s",
                str(result)[:300] if result else "(no result)",
            )
        except Exception as exc:
            self.logger.error("LLM bidding agent FAILED: %s", exc)

        self.logger.info("=== CLOSED BID PHASE COMPLETE ===")


# ---------------------------------------------------------------------------
# Standalone entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    agent = BiddingAgent()
    asyncio.run(agent.run())
