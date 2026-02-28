"""
Hackapizza 2.0 — Phase-driven Restaurant Management Agent
=========================================================
Connects to the SSE event stream, reacts to phase transitions,
and uses an AI agent + MCP tools to execute the right strategy per phase.

Phases (in order):
  speaking → closed_bid → waiting → serving → stopped
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from datapizza.agents import Agent
from datapizza.clients.openai_like import OpenAILikeClient
from datapizza.tools.mcp_client import MCPClient

from hp2.core.api import (
    GamePhase,
    HackapizzaClient,
    ClientOrder,
    IncomingMessage,
)
from hp2.core.settings import get_settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("hackapizza")

# ---------------------------------------------------------------------------
# Settings & constants
# ---------------------------------------------------------------------------
settings = get_settings()
HACKAPIZZA_BASE_URL = "https://hackapizza.datapizza.tech"
MCP_ENDPOINT = f"{HACKAPIZZA_BASE_URL}/mcp"

# ---------------------------------------------------------------------------
# System prompt — shared context for every agent invocation
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are the autonomous AI manager of the restaurant "RAGù" (team 6) in Hackapizza 2.0.
Your SOLE objective is to maximise the restaurant's final balance.

KEY RULES:
• Ingredients expire at the end of every turn — only buy what you will use.
• Serving a dish that conflicts with a customer's intolerances → federal sanctions, $0 payment.
• A closed restaurant (or one with an empty menu) receives NO customers.
• In the blind auction, the highest bidder gets priority; you may receive less than requested.
• Customer archetypes: Galactic Explorer (cheap & fast), Astrobaron (quality & fast, high budget),
  Cosmic Sage (prestige, patient, high budget), Orbital Family (balanced, patient, medium budget).

AVAILABLE MCP TOOLS:
  closed_bid        — place blind-auction bids (list of {ingredient, bid, quantity})
  save_menu         — set menu items (list of {name, price})
  create_market_entry — P2P buy/sell (side, ingredient_name, quantity, price)
  execute_transaction — accept a P2P market offer (market_entry_id)
  delete_market_entry — cancel your P2P offer (market_entry_id)
  prepare_dish      — start cooking a dish (dish_name)
  serve_dish        — serve a ready dish to a customer (dish_name, client_id)
  update_restaurant_is_open — open/close the restaurant (is_open: bool)
  send_message      — message another team (recipient_id: int >0, text: str)

Always act decisively and quickly. Explain your reasoning briefly, then call tools."""


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  GameState — mutable snapshot refreshed from the server each phase      ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
class GameState:
    """Lightweight mutable store for the current turn's data."""

    def __init__(self):
        self.current_phase: GamePhase = GamePhase.UNKNOWN
        self.turn_id: Optional[str] = None
        self.balance: float = 0.0
        self.reputation: float = 0.0
        self.inventory: Dict[str, Any] = {}
        self.menu: List[Dict[str, Any]] = []
        self.kitchen: List[Dict[str, Any]] = []
        self.recipes: List[Dict[str, Any]] = []
        self.restaurants: List[Dict[str, Any]] = []
        self.market_entries: List[Dict[str, Any]] = []
        self.pending_customers: List[ClientOrder] = []
        self.prepared_dishes: List[str] = []
        self.messages: List[IncomingMessage] = []

    # --- helpers ---
    def customer_list_json(self) -> str:
        return json.dumps(
            [{"name": c.client_name, "order": c.order_text} for c in self.pending_customers],
            indent=2,
        )

    def summary(self) -> str:
        return json.dumps(
            {
                "phase": self.current_phase.value,
                "turn_id": self.turn_id,
                "balance": self.balance,
                "reputation": self.reputation,
                "inventory": self.inventory,
                "menu": self.menu,
                "kitchen": self.kitchen,
                "pending_customers": [
                    {"name": c.client_name, "order": c.order_text}
                    for c in self.pending_customers
                ],
                "prepared_dishes": self.prepared_dishes,
            },
            indent=2,
            default=str,
        )


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  PhaseManager — orchestrates agent actions per game phase               ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
class PhaseManager:
    """Routes SSE phase-change events to the matching handler and runs the agent."""

    def __init__(self, client: HackapizzaClient, agent: Agent, state: GameState):
        self.client = client
        self.agent = agent
        self.state = state
        self._agent_lock = asyncio.Lock()  # serialise agent calls

    # ---- server data refresh --------------------------------------------------

    async def refresh_state(self):
        """Pull the latest restaurant / market / recipe data from the server."""
        try:
            restaurant = await self.client.get_my_restaurant()
            self.state.balance = restaurant.balance
            self.state.reputation = restaurant.reputation
            self.state.inventory = restaurant.inventory
            self.state.menu = [
                {"name": i.name, "price": i.price} for i in restaurant.menu.items
            ]
            self.state.kitchen = restaurant.kitchen
        except Exception as e:
            logger.error(f"refresh_state (restaurant) failed: {e}")

        try:
            self.state.recipes = [
                r.model_dump(by_alias=True) for r in await self.client.get_recipes()
            ]
        except Exception as e:
            logger.error(f"refresh_state (recipes) failed: {e}")

        try:
            self.state.restaurants = [
                r.model_dump(by_alias=True) for r in await self.client.get_restaurants()
            ]
        except Exception as e:
            logger.error(f"refresh_state (restaurants) failed: {e}")

        try:
            self.state.market_entries = [
                m.model_dump(by_alias=True) for m in await self.client.get_market_entries()
            ]
        except Exception as e:
            logger.error(f"refresh_state (market) failed: {e}")

    # ---- safe agent invocation ------------------------------------------------

    async def _run_agent(self, prompt: str) -> Any:
        """Run the agent with a lock so we never issue two calls in parallel."""
        async with self._agent_lock:
            try:
                result = await self.agent.a_run(prompt)
                return result
            except Exception as e:
                logger.error(f"Agent error: {e}", exc_info=True)
                return None

    # ---- top-level dispatcher -------------------------------------------------

    async def handle_phase(self, phase: GamePhase):
        """Entry-point called by the SSE callback on every phase change."""
        self.state.current_phase = phase
        await self.refresh_state()

        handlers = {
            GamePhase.SPEAKING: self._handle_speaking,
            GamePhase.CLOSED_BID: self._handle_closed_bid,
            GamePhase.WAITING: self._handle_waiting,
            GamePhase.SERVING: self._handle_serving,
            GamePhase.STOPPED: self._handle_stopped,
        }
        handler = handlers.get(phase)
        if handler:
            logger.info("=" * 60)
            logger.info(f"  PHASE ▸ {phase.value.upper()}")
            logger.info("=" * 60)
            await handler()
        else:
            logger.warning(f"No handler for phase: {phase}")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 — SPEAKING
    # ──────────────────────────────────────────────────────────────────────────
    async def _handle_speaking(self):
        prompt = f"""\
PHASE: SPEAKING — Negotiation & Planning

== Restaurant state ==
{self.state.summary()}

== All recipes (name · ingredients · prep_time_ms · prestige) ==
{json.dumps(self.state.recipes, indent=2, default=str)}

== Other restaurants ==
{json.dumps(self.state.restaurants, indent=2, default=str)}

YOUR TASKS (in order):
1. Analyse available recipes.  Pick 3-5 target recipes that balance prestige, ingredient
   count and preparation time. Prefer recipes whose ingredients overlap (so fewer bids).
2. Decide a rough bidding budget (keep ≥30 % of balance as safety margin).
3. Optionally send messages to other teams to propose trades or alliances
   (use send_message — recipient_id must be >0).
4. Summarise your plan: which dishes, target ingredients, pricing strategy.
"""
        response = await self._run_agent(prompt)
        logger.info(f"[SPEAKING] Agent finished — {_short(response)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 — CLOSED BID
    # ──────────────────────────────────────────────────────────────────────────
    async def _handle_closed_bid(self):
        await self.refresh_state()
        prompt = f"""\
PHASE: CLOSED BID — Blind Auction

== Restaurant state ==
{self.state.summary()}

== All recipes ==
{json.dumps(self.state.recipes, indent=2, default=str)}

== P2P market entries (may be empty early in turn) ==
{json.dumps(self.state.market_entries, indent=2, default=str)}

YOUR TASKS:
1. From the recipes you planned in SPEAKING, compile the full ingredient list you need.
2. For each ingredient decide quantity and bid price.
   Rule of thumb: bid ~5-15 % of expected dish revenue per unit, never exceed what you
   can recover by selling the dish.
3. Call closed_bid ONCE with all bids:
      closed_bid(bids=[{{"ingredient": "Name", "bid": <int>, "quantity": <int>}}, ...])
   - bid and quantity must be integers > 0.
4. Also save your planned menu NOW using save_menu so it is ready for serving:
      save_menu(items=[{{"name": "Dish Name", "price": <float>}}, ...])
   Set prices above ingredient cost but attractive for your target archetype.

BUDGET: Your balance is {self.state.balance}. Do NOT spend more than 60 % on bids.
"""
        response = await self._run_agent(prompt)
        logger.info(f"[CLOSED BID] Agent finished — {_short(response)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 3 — WAITING
    # ──────────────────────────────────────────────────────────────────────────
    async def _handle_waiting(self):
        await self.refresh_state()
        prompt = f"""\
PHASE: WAITING — Post-Auction Setup

== Restaurant state (inventory now reflects auction results) ==
{self.state.summary()}

== All recipes ==
{json.dumps(self.state.recipes, indent=2, default=str)}

== P2P market entries ==
{json.dumps(self.state.market_entries, indent=2, default=str)}

YOUR TASKS:
1. Inspect your inventory. Determine which recipes you CAN actually make.
2. Update your menu with save_menu to list ONLY dishes you have full ingredients for.
   Remove any dish you cannot prepare — an empty promise hurts reputation.
3. Adjust prices if needed (e.g., raise prices if you have rare high-prestige dishes).
4. Open the restaurant: update_restaurant_is_open(is_open=true).
5. If you are missing a critical ingredient, browse market_entries and use
   execute_transaction to buy it.  If you have surplus, create_market_entry to sell.

REMEMBER: Ingredients expire at turn end. Unsold surplus = pure loss.
"""
        response = await self._run_agent(prompt)
        logger.info(f"[WAITING] Agent finished — {_short(response)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 4 — SERVING
    # ──────────────────────────────────────────────────────────────────────────
    async def _handle_serving(self):
        await self.refresh_state()
        prompt = f"""\
PHASE: SERVING — Doors Are Open

== Restaurant state ==
{self.state.summary()}

== Pending customer orders ==
{self.state.customer_list_json()}

== All recipes ==
{json.dumps(self.state.recipes, indent=2, default=str)}

YOUR TASKS:
1. Ensure the restaurant is OPEN: update_restaurant_is_open(is_open=true).
2. For every pending customer:
   a) Read their order text carefully — look for intolerances / allergen hints.
   b) Pick a dish from your menu that matches their request and does NOT
      violate any intolerance.
   c) Call prepare_dish(dish_name=<name>) to start cooking.
      (You will be notified when cooking finishes, then you can serve.)
3. If you run low on ingredients or get overwhelmed, close the restaurant
   temporarily to protect reputation.
4. Watch out: prepare_dish is asynchronous — it fires a preparation_complete
   event when done.  You can queue multiple preparations.

CRITICAL: Never serve a dish that conflicts with a customer's intolerances!
"""
        response = await self._run_agent(prompt)
        logger.info(f"[SERVING] Agent finished — {_short(response)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 5 — STOPPED
    # ──────────────────────────────────────────────────────────────────────────
    async def _handle_stopped(self):
        await self.refresh_state()
        logger.info(
            f"[STOPPED] Turn ended — Balance: {self.state.balance}, "
            f"Reputation: {self.state.reputation}"
        )
        # Reset per-turn volatile state
        self.state.pending_customers.clear()
        self.state.prepared_dishes.clear()
        self.state.inventory.clear()

    # ──────────────────────────────────────────────────────────────────────────
    # Real-time event handlers (called from SSE callbacks)
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_customer(self, order: ClientOrder):
        """A new customer appeared during the serving phase."""
        self.state.pending_customers.append(order)
        logger.info(f"[CUSTOMER] {order.client_name}: {order.order_text}")

        if self.state.current_phase != GamePhase.SERVING:
            logger.info("[CUSTOMER] Not in SERVING phase — queued for later.")
            return

        await self.refresh_state()
        prompt = f"""\
🚨 NEW CUSTOMER ARRIVED during SERVING phase!

Customer name : {order.client_name}
Request       : {order.order_text}

== Current restaurant state ==
{self.state.summary()}

== All recipes ==
{json.dumps(self.state.recipes, indent=2, default=str)}

TASK:
1. Match the request to a dish on your menu that you have ingredients for.
2. Check for intolerance keywords in the request — do NOT serve conflicting food.
3. Call prepare_dish(dish_name=<matched_dish>) to start cooking.
4. After the dish is prepared (you'll be notified), call
   serve_dish(dish_name=<dish>, client_id=<customer name or id>).
"""
        response = await self._run_agent(prompt)
        logger.info(f"[CUSTOMER] Handled {order.client_name} — {_short(response)}")

    async def handle_preparation_complete(self, dish_name: str):
        """A dish finished cooking — try to serve it immediately."""
        self.state.prepared_dishes.append(dish_name)
        logger.info(f"[PREP DONE] {dish_name}")

        if self.state.current_phase != GamePhase.SERVING:
            return
        if not self.state.pending_customers:
            logger.info("[PREP DONE] No pending customers to serve.")
            return

        await self.refresh_state()
        prompt = f"""\
A dish just finished cooking: "{dish_name}"

Pending customers still waiting:
{self.state.customer_list_json()}

TASK: Serve the dish to the most appropriate waiting customer.
Call serve_dish(dish_name="{dish_name}", client_id=<best matching customer>).
"""
        response = await self._run_agent(prompt)
        logger.info(f"[SERVE] {dish_name} — {_short(response)}")

    async def handle_message(self, msg: IncomingMessage):
        """Another team sent us a message."""
        self.state.messages.append(msg)
        logger.info(
            f"[MSG] From {msg.sender_name} (id={msg.sender_id}): {msg.text}"
        )


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  Utility                                                                ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
def _short(result: Any, max_len: int = 200) -> str:
    """Return a truncated string representation, safe for logging."""
    s = str(result) if result else "(no result)"
    return s[:max_len] + ("…" if len(s) > max_len else "")


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  Main entry-point                                                       ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
async def main():
    """Wire everything together and start the SSE event loop."""
    logger.info("🍕 Hackapizza 2.0 Agent Starting…")
    logger.info(f"   Team ID : {settings.hackapizza_team_id}")
    logger.info(f"   Model   : gpt-oss-120b (Regolo AI)")
    logger.info("-" * 60)

    # ── 1. MCP tools (for the Agent) ─────────────────────────────────────────
    mcp_client = MCPClient(
        url=MCP_ENDPOINT,
        headers={"x-api-key": settings.hackapizza_team_api_key},
        timeout=30,
    )
    mcp_tools = mcp_client.list_tools()
    logger.info(f"Loaded {len(mcp_tools)} MCP tools: {[t.name for t in mcp_tools]}")

    # ── 2. LLM client ────────────────────────────────────────────────────────
    regolo_client = OpenAILikeClient(
        api_key=settings.regolo_api_key,
        model="gpt-oss-120b",
        system_prompt=SYSTEM_PROMPT,
        base_url="https://api.regolo.ai/v1",
    )

    # ── 3. Agent ──────────────────────────────────────────────────────────────
    agent = Agent(
        name="hackapizza_agent",
        client=regolo_client,
        tools=mcp_tools,
        max_steps=15,
    )

    # ── 4. SSE client (drives the event loop) ────────────────────────────────
    hackapizza_client = HackapizzaClient(
        team_id=settings.hackapizza_team_id,
        api_key=settings.hackapizza_team_api_key,
    )

    # ── 5. Game state + phase manager ─────────────────────────────────────────
    game_state = GameState()
    phase_mgr = PhaseManager(hackapizza_client, agent, game_state)

    # ── 6. Register SSE event callbacks ───────────────────────────────────────

    @hackapizza_client.on_game_started
    async def _on_game_started(data: Dict[str, Any]):
        turn_id = data.get("turnId") or data.get("turn_id") or data.get("value")
        game_state.turn_id = str(turn_id) if turn_id else None
        logger.info(f"🎮 GAME STARTED — turn_id={game_state.turn_id}")

    @hackapizza_client.on_phase_changed
    async def _on_phase_changed(phase: GamePhase):
        logger.info(f"⏩ Phase changed → {phase.value}")
        await phase_mgr.handle_phase(phase)

    @hackapizza_client.on_client_spawned
    async def _on_client_spawned(order: ClientOrder):
        await phase_mgr.handle_customer(order)

    @hackapizza_client.on_preparation_complete
    async def _on_prep_complete(dish: str):
        await phase_mgr.handle_preparation_complete(dish)

    @hackapizza_client.on_new_message
    async def _on_new_message(msg: IncomingMessage):
        await phase_mgr.handle_message(msg)

    # ── 7. Start the blocking SSE event loop ──────────────────────────────────
    logger.info("Connecting to SSE event stream…")
    while True:
        try:
            await hackapizza_client.start()
        except Exception as e:
            logger.error(f"SSE connection lost: {e} — reconnecting in 5 s…")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
