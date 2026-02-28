"""
Hackapizza 2.0 Agent SDK
Strictly typed, event-driven client for the Hackapizza Gastronomic Multiverse.
"""

import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
import os
from typing import Any, Callable, Awaitable, Dict, List, Optional

import aiohttp

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GamePhase(str, Enum):
    """The sequential phases of a game turn."""
    SPEAKING = "speaking"
    CLOSED_BID = "closed_bid"
    WAITING = "waiting"
    SERVING = "serving"
    STOPPED = "stopped"
    UNKNOWN = "unknown"

class MarketSide(str, Enum):
    """Direction of a P2P market transaction."""
    BUY = "BUY"
    SELL = "SELL"

# ---------------------------------------------------------------------------
# Data Models (Strict Typing)
# ---------------------------------------------------------------------------

@dataclass
class BidRequest:
    """Represents a request to buy an ingredient during the blind auction."""
    ingredient: str
    bid: float
    quantity: int

@dataclass
class MenuItem:
    """A dish offered on your restaurant's menu."""
    name: str
    price: float

@dataclass
class ClientOrder:
    """Incoming order from a Multiverse customer."""
    client_name: str
    order_text: str

@dataclass
class IncomingMessage:
    """Direct message from another team."""
    message_id: str
    sender_id: str
    sender_name: str
    text: str
    datetime: str

# ---------------------------------------------------------------------------
# The SDK Client
# ---------------------------------------------------------------------------

class HackapizzaClient:
    """
    The main SDK client for interacting with the Hackapizza server.
    Manages API calls, MCP tool execution, and the SSE event loop.
    """

    def __init__(self, team_id: int | None, api_key: str | None, base_url: str = "https://hackapizza.datapizza.tech"):
        self.team_id = team_id or os.getenv("HACKAPIZZA_TEAM_ID")
        self.api_key = api_key or os.getenv("HACKAPIZZA_TEAM_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.logger = logging.getLogger(f"HackapizzaClient[{self.team_id}]")
        
        self._headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json"
        }
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Event Callbacks
        self._on_game_started: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._on_phase_changed: Optional[Callable[[GamePhase], Awaitable[None]]] = None
        self._on_client_spawned: Optional[Callable[[ClientOrder], Awaitable[None]]] = None
        self._on_preparation_complete: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_message: Optional[Callable[[str, Any], Awaitable[None]]] = None
        self._on_new_message: Optional[Callable[[IncomingMessage], Awaitable[None]]] = None

    # --- Decorators for Event Registration ---

    def on_game_started(self, func: Callable[[Dict[str, Any]], Awaitable[None]]):
        self._on_game_started = func
        return func

    def on_phase_changed(self, func: Callable[[GamePhase], Awaitable[None]]):
        self._on_phase_changed = func
        return func

    def on_client_spawned(self, func: Callable[[ClientOrder], Awaitable[None]]):
        self._on_client_spawned = func
        return func

    def on_preparation_complete(self, func: Callable[[str], Awaitable[None]]):
        self._on_preparation_complete = func
        return func

    def on_new_message(self, func: Callable[[IncomingMessage], Awaitable[None]]):
        self._on_new_message = func
        return func

    # --- HTTP Data Endpoints ---

    async def get_meals(self, turn_id: str) -> List[Dict[str, Any]]:
        """Fetch meals/customer requests for the current turn."""
        return await self._http_get(f"/meals?turn_id={turn_id}&restaurant_id={self.team_id}")

    async def get_restaurants(self) -> List[Dict[str, Any]]:
        """Overview of all active restaurants in the game."""
        return await self._http_get("/restaurants")

    async def get_recipes(self) -> List[Dict[str, Any]]:
        """List of all available recipes, their ingredients, and prep times."""
        return await self._http_get("/recipes")

    async def get_bid_history(self, turn_id: str) -> List[Dict[str, Any]]:
        """Historical blind auction bids for a given turn."""
        return await self._http_get(f"/bid_history?turn_id={turn_id}")

    async def get_my_restaurant(self) -> Dict[str, Any]:
        """Fetch balance, reputation, and inventory for your restaurant."""
        return await self._http_get(f"/restaurant/{self.team_id}")

    async def get_my_menu(self) -> List[Dict[str, Any]]:
        """Fetch the current menu active for your restaurant."""
        return await self._http_get(f"/restaurant/{self.team_id}/menu")

    async def get_market_entries(self) -> List[Dict[str, Any]]:
        """Fetch active and closed P2P market entries."""
        return await self._http_get("/market/entries")

    # --- MCP Tools (Action Endpoints) ---

    async def submit_closed_bids(self, bids: List[BidRequest]) -> Any:
        """Phase: closed_bid. Send your blind auction offers."""
        return await self._mcp_call("closed_bid", bids=[asdict(b) for b in bids])

    async def save_menu(self, items: List[MenuItem]) -> Any:
        """Phase: speaking, closed_bid, waiting. Set your menu and prices."""
        return await self._mcp_call("save_menu", items=[asdict(i) for i in items])

    async def create_market_entry(self, side: MarketSide, ingredient_name: str, quantity: int, price: float) -> Any:
        """Phase: all EXCEPT stopped. Create a P2P market offer."""
        return await self._mcp_call(
            "create_market_entry", 
            side=side.value, 
            ingredient_name=ingredient_name, 
            quantity=quantity, 
            price=price
        )

    async def execute_transaction(self, market_entry_id: int) -> Any:
        """Phase: all EXCEPT stopped. Fulfill another team's market offer."""
        return await self._mcp_call("execute_transaction", market_entry_id=market_entry_id)

    async def delete_market_entry(self, market_entry_id: int) -> Any:
        """Phase: all EXCEPT stopped. Remove your active P2P market offer."""
        return await self._mcp_call("delete_market_entry", market_entry_id=market_entry_id)

    async def prepare_dish(self, dish_name: str) -> Any:
        """Phase: serving. Start cooking a dish."""
        return await self._mcp_call("prepare_dish", dish_name=dish_name)

    async def serve_dish(self, dish_name: str, client_id: str) -> Any:
        """Phase: serving. Serve a completed dish to a specific customer."""
        return await self._mcp_call("serve_dish", dish_name=dish_name, client_id=client_id)

    async def set_restaurant_open_status(self, is_open: bool) -> Any:
        """Phase: all (serving is close-only). Open or close the restaurant to avoid collapse."""
        return await self._mcp_call("update_restaurant_is_open", is_open=is_open)

    async def send_direct_message(self, recipient_id: int, text: str) -> Any:
        """Phase: all EXCEPT stopped. Send a DM to another team."""
        return await self._mcp_call("send_message", recipient_id=recipient_id, text=text)

    # --- Internal HTTP / Connection Management ---

    async def _http_get(self, endpoint: str) -> Any:
        """Helper for GET requests."""
        if not self._session:
            raise RuntimeError("Client session not initialized. Run within context manager or start().")
        async with self._session.get(f"{self.base_url}{endpoint}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _mcp_call(self, tool_name: str, **kwargs) -> Any:
        """Helper to execute JSON-RPC calls against the MCP endpoint."""
        if not self._session:
            raise RuntimeError("Client session not initialized.")
            
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": kwargs
            },
            "id": 1 # ID can be static for simple single-threaded request/response
        }
        
        self.logger.debug(f"MCP Call -> {tool_name} with args {kwargs}")
        async with self._session.post(f"{self.base_url}/mcp", json=payload) as resp:
            if resp.status == 401:
                raise PermissionError("401 Unauthorized: Invalid API Key")
                
            resp.raise_for_status()
            data = await resp.json()
            
            result = data.get("result", {})
            if result.get("isError"):
                error_msg = result.get("content", [{}])[0].get("text", "Unknown MCP Tool Error")
                raise RuntimeError(f"MCP Error on '{tool_name}': {error_msg}")
                
            return result

    # --- Event Loop / SSE Parsing ---

    async def start(self):
        """Connects to the SSE endpoint and begins routing events to your callbacks."""
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
        
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers) as session:
            self._session = session
            url = f"{self.base_url}/events/{self.team_id}"
            self.logger.info(f"Connecting to SSE Event Stream: {url}")
            
            headers = {"Accept": "text/event-stream"}
            
            try:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    self.logger.info("SSE Connection Established.")
                    
                    async for raw_line in response.content:
                        await self._parse_sse_line(raw_line)
            except Exception as e:
                self.logger.error(f"SSE Connection dropped: {e}")
            finally:
                self._session = None

    async def _parse_sse_line(self, raw_line: bytes):
        if not raw_line:
            return

        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line or not line.startswith("data:"):
            return

        payload = line[5:].strip()
        if payload == "connected":
            self.logger.info("Server handshake: Connected")
            return

        try:
            event_json = json.loads(payload)
            event_type = event_json.get("type")
            data = event_json.get("data", {})
            
            # Normalize single values to dict for consistent handling
            if not isinstance(data, dict):
                data = {"value": data}
                
            await self._dispatch_event(event_type, data)
            
        except json.JSONDecodeError:
            self.logger.warning(f"Failed to parse SSE line: {payload}")

    async def _dispatch_event(self, event_type: str, data: Dict[str, Any]):
        try:
            if event_type == "game_started" and self._on_game_started:
                await self._on_game_started(data)
                
            elif event_type == "game_phase_changed" and self._on_phase_changed:
                try:
                    phase = GamePhase(data.get("phase", "unknown"))
                except ValueError:
                    phase = GamePhase.UNKNOWN
                await self._on_phase_changed(phase)
                
            elif event_type == "client_spawned" and self._on_client_spawned:
                order = ClientOrder(
                    client_name=data.get("clientName", "unknown"),
                    order_text=data.get("orderText", "unknown")
                )
                await self._on_client_spawned(order)
                
            elif event_type == "preparation_complete" and self._on_preparation_complete:
                await self._on_preparation_complete(data.get("dish", "unknown"))
                
            elif event_type == "new_message" and self._on_new_message:
                msg = IncomingMessage(
                    message_id=data.get("messageId", ""),
                    sender_id=data.get("senderId", ""),
                    sender_name=data.get("senderName", ""),
                    text=data.get("text", ""),
                    datetime=data.get("datetime", "")
                )
                await self._on_new_message(msg)
                
            # Log heartbeats silently, don't dispatch unless needed
            elif event_type == "heartbeat":
                pass 
                
        except Exception as e:
            self.logger.error(f"Error in handler for {event_type}: {e}", exc_info=True)
