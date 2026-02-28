"""
Hackapizza 2.0 Agent SDK
Strictly typed, event-driven client for the Hackapizza Gastronomic Multiverse.
"""

import asyncio
import json
import logging
from functools import wraps
from dataclasses import asdict, dataclass
from enum import Enum
from time import perf_counter
from typing import Any, Awaitable, Callable, Concatenate, Dict, List, Optional, ParamSpec, TypeVar, cast

import aiohttp
import websockets
from pydantic import TypeAdapter

from .schema import (
    BidHistoryResponseSchema,
    MarketEntriesResponseSchema,
    MealsResponseSchema,
    MyMenuResponseSchema,
    MyRestaurantResponseSchema,
    RecipesResponseSchema,
    RestaurantsResponseSchema,
)
from .settings import get_settings, get_sql_logging_settings
from .sql_logging_mixin import SqlLoggingMixin

_RECIPES_ADAPTER = TypeAdapter(RecipesResponseSchema)
_RESTAURANTS_ADAPTER = TypeAdapter(RestaurantsResponseSchema)
_MY_RESTAURANT_ADAPTER = TypeAdapter(MyRestaurantResponseSchema)
_MY_MENU_ADAPTER = TypeAdapter(MyMenuResponseSchema)
_MARKET_ENTRIES_ADAPTER = TypeAdapter(MarketEntriesResponseSchema)
_MEALS_ADAPTER = TypeAdapter(MealsResponseSchema)
_BID_HISTORY_ADAPTER = TypeAdapter(BidHistoryResponseSchema)

P = ParamSpec("P")
T = TypeVar("T")


def typed_endpoint(
    adapter: TypeAdapter[T], *, persist_method_name: str | None = None
) -> Callable[
    [Callable[Concatenate["HackapizzaClient", P], Awaitable[str]]],
    Callable[Concatenate["HackapizzaClient", P], Awaitable[T]],
]:
    """Decorator for typed HTTP GET endpoints with optional typed persistence hook."""

    def decorator(
        func: Callable[Concatenate["HackapizzaClient", P], Awaitable[str]]
    ) -> Callable[Concatenate["HackapizzaClient", P], Awaitable[T]]:
        @wraps(func)
        async def wrapper(self: "HackapizzaClient", *args: P.args, **kwargs: P.kwargs) -> T:
            endpoint = await func(self, *args, **kwargs)
            result = await self._http_get_typed(
                endpoint, adapter, persist_method_name=persist_method_name
            )
            return cast(T, result)

        return wrapper

    return decorator

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

    client_id: str
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


class HackapizzaClient(SqlLoggingMixin):
    """
    The main SDK client for interacting with the Hackapizza server.
    Manages API calls, MCP tool execution, and the SSE event loop.
    """

    def __init__(
        self,
        team_id: int | None,
        api_key: str | None,
        base_url: str = "https://hackapizza.datapizza.tech",
        *,
        enable_sql_logging: bool = False,
        sql_connstr: str | None = None,
    ):
        settings = get_settings() if team_id is None or api_key is None else None
        self.team_id = team_id or settings.hackapizza_team_id  # type: ignore
        self.api_key = api_key or settings.hackapizza_team_api_key  # type: ignore
        self.base_url = base_url.rstrip("/")
        self.logger = logging.getLogger(f"HackapizzaClient[{self.team_id}]")
        self._sql_logging_enabled = enable_sql_logging

        if self._sql_logging_enabled:
            resolved_sql_connstr = sql_connstr
            if resolved_sql_connstr is None:
                resolved_sql_connstr = get_sql_logging_settings().hackapizza_sql_connstr
            self._init_sql_logging(resolved_sql_connstr)

        self._headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            # MCP endpoint requires client to accept both payload and event-stream.
            "Accept": "application/json, text/event-stream",
        }
        self._session: Optional[aiohttp.ClientSession] = None

        # Event Callbacks
        self._on_game_started: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._on_phase_changed: Optional[Callable[[GamePhase], Awaitable[None]]] = None
        self._on_client_order: Optional[Callable[[ClientOrder], Awaitable[None]]] = None
        self._on_preparation_complete: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_new_message: Optional[Callable[[IncomingMessage], Awaitable[None]]] = None

    # --- Decorators for Event Registration ---

    def on_game_started(self, func: Callable[[Dict[str, Any]], Awaitable[None]]):
        self._on_game_started = func
        return func

    def on_phase_changed(self, func: Callable[[GamePhase], Awaitable[None]]):
        self._on_phase_changed = func
        return func

    def on_client_order(self, func: Callable[[ClientOrder], Awaitable[None]]):
        self._on_client_order = func
        return func

    def on_preparation_complete(self, func: Callable[[str], Awaitable[None]]):
        self._on_preparation_complete = func
        return func

    def on_new_message(self, func: Callable[[IncomingMessage], Awaitable[None]]):
        self._on_new_message = func
        return func

    # --- HTTP Data Endpoints ---

    @typed_endpoint(_MEALS_ADAPTER, persist_method_name="_persist_typed_meals")
    async def _get_meals_typed(self, turn_id: str) -> str:
        """Fetch meals/customer requests for the current turn."""
        return f"/meals?turn_id={turn_id}&restaurant_id={self.team_id}"

    async def get_meals(self, turn_id: str) -> MealsResponseSchema:
        return await self._get_meals_typed(turn_id)

    @typed_endpoint(_RESTAURANTS_ADAPTER, persist_method_name="_persist_typed_restaurants")
    async def _get_restaurants_typed(self) -> str:
        """Overview of all active restaurants in the game."""
        return "/restaurants"

    async def get_meals_raw(self, turn_id: str) -> Any:
        """Fetch meals as raw JSON (untyped) to inspect all fields."""
        return await self._http_get(f"/meals?turn_id={turn_id}&restaurant_id={self.team_id}")

    async def get_restaurants(self) -> RestaurantsResponseSchema:
        return await self._get_restaurants_typed()

    @typed_endpoint(_RECIPES_ADAPTER, persist_method_name="_persist_typed_recipes")
    async def _get_recipes_typed(self) -> str:
        """List of all available recipes, their ingredients, and prep times."""
        return "/recipes"

    async def get_recipes(self) -> RecipesResponseSchema:
        return await self._get_recipes_typed()

    @typed_endpoint(_BID_HISTORY_ADAPTER, persist_method_name="_persist_typed_bid_history")
    async def _get_bid_history_typed(self, turn_id: str) -> str:
        """Historical blind auction bids for a given turn."""
        return f"/bid_history?turn_id={turn_id}"

    async def get_bid_history(self, turn_id: str) -> BidHistoryResponseSchema:
        return await self._get_bid_history_typed(turn_id)

    @typed_endpoint(_MY_RESTAURANT_ADAPTER, persist_method_name="_persist_typed_my_restaurant")
    async def _get_my_restaurant_typed(self) -> str:
        """Fetch balance, reputation, and inventory for your restaurant."""
        return f"/restaurant/{self.team_id}"

    async def get_my_restaurant(self) -> MyRestaurantResponseSchema:
        return await self._get_my_restaurant_typed()

    @typed_endpoint(_MY_MENU_ADAPTER)
    async def _get_my_menu_typed(self) -> str:
        """Fetch the current menu active for your restaurant."""
        return f"/restaurant/{self.team_id}/menu"

    async def get_my_menu(self) -> MyMenuResponseSchema:
        return await self._get_my_menu_typed()

    @typed_endpoint(_MARKET_ENTRIES_ADAPTER, persist_method_name="_persist_typed_market_entries")
    async def _get_market_entries_typed(self) -> str:
        """Fetch active and closed P2P market entries."""
        return "/market/entries"

    async def get_market_entries(self) -> MarketEntriesResponseSchema:
        return await self._get_market_entries_typed()

    # --- MCP Tools (Action Endpoints) ---

    async def submit_closed_bids(self, bids: List[BidRequest]) -> Any:
        """Phase: closed_bid. Send your blind auction offers."""
        return await self._mcp_call("closed_bid", bids=[asdict(b) for b in bids])

    async def save_menu(self, items: List[MenuItem]) -> Any:
        """Phase: speaking, closed_bid, waiting. Set your menu and prices."""
        return await self._mcp_call("save_menu", items=[asdict(i) for i in items])

    async def create_market_entry(
        self, side: MarketSide, ingredient_name: str, quantity: int, price: float
    ) -> Any:
        """Phase: all EXCEPT stopped. Create a P2P market offer."""
        return await self._mcp_call(
            "create_market_entry",
            side=side.value,
            ingredient_name=ingredient_name,
            quantity=quantity,
            price=price,
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

    async def _http_get(self, endpoint: str, *, _include_call_id: bool = False) -> Any:
        """Helper for GET requests."""
        if not self._session:
            raise RuntimeError(
                "Client session not initialized. Run within context manager or start()."
            )

        started = perf_counter()
        turn_id = self._extract_turn_id_from_endpoint(endpoint)

        try:
            async with self._session.get(f"{self.base_url}{endpoint}") as resp:
                resp.raise_for_status()
                payload = await resp.json()
        except Exception as e:
            self._safe_log_call(
                source="http_get",
                name=endpoint,
                status="error",
                duration_ms=(perf_counter() - started) * 1000,
                turn_id=turn_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

        call_id = self._safe_log_call(
            source="http_get",
            name=endpoint,
            status="ok",
            duration_ms=(perf_counter() - started) * 1000,
            turn_id=turn_id,
        )
        if _include_call_id:
            return payload, call_id
        return payload

    async def _http_get_typed(
        self,
        endpoint: str,
        adapter: TypeAdapter[T],
        *,
        persist_method_name: str | None = None,
    ) -> T:
        """GET + pydantic validation to guarantee typed endpoint responses."""
        payload, call_id = await self._http_get(endpoint, _include_call_id=True)
        typed_payload = adapter.validate_python(payload)

        if self._sql_logging_enabled and call_id and persist_method_name:
            try:
                persist_method = getattr(self, persist_method_name, None)
                if callable(persist_method):
                    persist_method(call_id=call_id, typed_payload=typed_payload)
            except Exception as log_exc:
                self.logger.debug("Typed persistence failed: %s", log_exc, exc_info=True)

        return cast(T, typed_payload)

    def _persist_typed_recipes(self, *, call_id: int, typed_payload: RecipesResponseSchema) -> None:
        self._persist_recipes(call_id=call_id, recipes=typed_payload)

    def _persist_typed_restaurants(
        self, *, call_id: int, typed_payload: RestaurantsResponseSchema
    ) -> None:
        self._persist_restaurants(call_id=call_id, restaurants=typed_payload)

    def _persist_typed_my_restaurant(
        self, *, call_id: int, typed_payload: MyRestaurantResponseSchema
    ) -> None:
        self._persist_restaurants(call_id=call_id, restaurants=[typed_payload])

    def _persist_typed_meals(self, *, call_id: int, typed_payload: MealsResponseSchema) -> None:
        self._persist_meals(call_id=call_id, meals=typed_payload)

    def _persist_typed_market_entries(
        self, *, call_id: int, typed_payload: MarketEntriesResponseSchema
    ) -> None:
        self._persist_market_entries(call_id=call_id, entries=typed_payload)

    def _persist_typed_bid_history(
        self, *, call_id: int, typed_payload: BidHistoryResponseSchema
    ) -> None:
        self._persist_bid_history(call_id=call_id, bids=typed_payload)

    async def _mcp_call(self, tool_name: str, **kwargs) -> Any:
        """Helper to execute JSON-RPC calls against the MCP endpoint."""
        if not self._session:
            raise RuntimeError("Client session not initialized.")

        started = perf_counter()
        turn_id = kwargs.get("turn_id")

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": kwargs},
            "id": 1,  # ID can be static for simple single-threaded request/response
        }

        self.logger.debug(f"MCP Call -> {tool_name} with args {kwargs}")
        try:
            async with self._session.post(f"{self.base_url}/mcp", json=payload) as resp:
                if resp.status == 401:
                    raise PermissionError("401 Unauthorized: Invalid API Key")

                resp.raise_for_status()
                data = await resp.json()

                result = data.get("result", {})
                if result.get("isError"):
                    error_msg = result.get("content", [{}])[0].get(
                        "text", "Unknown MCP Tool Error"
                    )
                    raise RuntimeError(f"MCP Error on '{tool_name}': {error_msg}")
        except Exception as e:
            self._safe_log_call(
                source="mcp_call",
                name=tool_name,
                status="error",
                duration_ms=(perf_counter() - started) * 1000,
                turn_id=turn_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

        self._safe_log_call(
            source="mcp_call",
            name=tool_name,
            status="ok",
            duration_ms=(perf_counter() - started) * 1000,
            turn_id=turn_id,
        )
        return result

    def _safe_log_call(
        self,
        *,
        source: str,
        name: str,
        status: str,
        duration_ms: float | None,
        turn_id: str | None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> int | None:
        if not self._sql_logging_enabled:
            return None
        try:
            return self._log_call_metadata(
                source=source,
                name=name,
                status=status,
                duration_ms=duration_ms,
                turn_id=turn_id,
                error_type=error_type,
                error_message=error_message,
            )
        except Exception as log_exc:
            self.logger.debug("SQL logging failed: %s", log_exc, exc_info=True)
            return None

    # --- Event Loop / SSE Parsing ---

    async def start_old_sse(self):
        """[Deprecated] Connects directly to the SSE endpoint. Use start() instead."""
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

                    # Read line-by-line (SSE is a line-oriented protocol)
                    while True:
                        raw_line = await response.content.readline()
                        if not raw_line:  # EOF
                            break
                        await self._parse_sse_line(raw_line)
            except Exception as e:
                self.logger.error(f"SSE Connection dropped: {e}")
            finally:
                self._session = None

    async def start(
        self,
        ws_url: str | None = None,
        *,
        retry_initial: float = 2.0,
        retry_max: float = 8.0,
        retry_factor: float = 2.0,
    ):
        """Connect to the event_logger WebSocket server instead of SSE directly.

        The WS server relays the same JSON payloads the SSE stream produces,
        so dispatching is identical.  An aiohttp session is still opened for
        HTTP GET / MCP POST calls against the Hackapizza API.

        Resilient: if the WS server is down or the connection drops, the
        client retries with exponential back-off (capped at *retry_max* s).
        """
        if ws_url is None:
            ws_url = get_settings().event_proxy_url

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
        backoff = retry_initial

        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers) as session:
            self._session = session

            while True:
                try:
                    self.logger.info(f"Connecting to event_logger WS: {ws_url}")
                    async with websockets.connect(ws_url) as ws:
                        self.logger.info("WS Connection Established.")
                        backoff = retry_initial  # reset on successful connect
                        async for raw_msg in ws:
                            try:
                                event_json = json.loads(raw_msg)
                                event_type = event_json.get("type")
                                data = event_json.get("data", {})
                                if not isinstance(data, dict):
                                    data = {"value": data}
                                await self._dispatch_event(event_type, data)
                            except json.JSONDecodeError:
                                self.logger.warning(
                                    f"Unparseable WS message: {str(raw_msg)[:200]}"
                                )
                except asyncio.CancelledError:
                    self.logger.info("WS listener cancelled, shutting down.")
                    break
                except Exception as e:
                    self.logger.warning(
                        f"WS connection lost ({type(e).__name__}: {e}). "
                        f"Retrying in {backoff:.0f}s…"
                    )

                await asyncio.sleep(backoff)
                backoff = min(backoff * retry_factor, retry_max)

            self._session = None

    async def _parse_sse_line(self, raw_line: bytes):
        if not raw_line:
            return

        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line:
            return

        # Handle SSE "data:" prefix format (used for handshake)
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "connected":
                self.logger.info("Server handshake: Connected")
                return
            json_str = payload
        elif line.startswith("{"):
            # Raw JSON line (used for heartbeats and game events)
            json_str = line
        else:
            # Ignore non-data lines (e.g. SSE comments, event: lines)
            return

        try:
            event_json = json.loads(json_str)
            event_type = event_json.get("type")
            data = event_json.get("data", {})

            # Normalize single values to dict for consistent handling
            if not isinstance(data, dict):
                data = {"value": data}

            await self._dispatch_event(event_type, data)

        except json.JSONDecodeError:
            self.logger.warning(f"Failed to parse SSE line: {json_str}")

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

            elif event_type == "client_spawned" and self._on_client_order:
                order = ClientOrder(
                    client_id=str(data.get("clientId", data.get("client_id", data.get("id", "unknown")))),
                    client_name=data.get("clientName", data.get("name", "unknown")),
                    order_text=data.get("orderText", data.get("order_text", data.get("text", "unknown"))),
                )
                await self._on_client_order(order)

            elif event_type == "preparation_complete" and self._on_preparation_complete:
                await self._on_preparation_complete(data.get("dish", "unknown"))

            elif event_type == "new_message" and self._on_new_message:
                msg = IncomingMessage(
                    message_id=data.get("messageId", ""),
                    sender_id=data.get("senderId", ""),
                    sender_name=data.get("senderName", ""),
                    text=data.get("text", ""),
                    datetime=data.get("datetime", ""),
                )
                await self._on_new_message(msg)

            elif event_type == "heartbeat":
                pass

        except Exception as e:
            self.logger.error(f"Error in handler for {event_type}: {e}", exc_info=True)
