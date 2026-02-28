"""Discovery harness for exercising HackapizzaClient endpoints.

This module can be used as a script to call all public API endpoints,
capture successes/failures, and persist a JSON report for inspection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

import aiohttp

from hp2.core.api import BidRequest, HackapizzaClient, MarketSide, MenuItem

LOGGER = logging.getLogger("api.discovery_harness")


@dataclass
class DiscoveryContext:
    turn_id: int
    ingredient_name: str = "tomato"
    dish_name: str = "margherita"
    client_id: str = "unknown-client"
    market_entry_id: int = -1
    recipient_id: int = 0


@dataclass
class EndpointCallResult:
    endpoint: str
    status: str
    duration_ms: float
    args: dict[str, Any]
    result: Any | None = None
    error_type: str | None = None
    error_message: str | None = None


def _safe_json(value: Any) -> Any:
    """Convert arbitrary Python values into JSON-safe structures."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _safe_json(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [_safe_json(v) for v in value]
        if hasattr(value, "__dict__"):
            return _safe_json(vars(value))
        return repr(value)


def _first_present(payload: dict[str, Any], candidates: list[str], default: Any) -> Any:
    for key in candidates:
        value = payload.get(key)
        if value is not None:
            return value
    return default


def _build_context_from_snapshots(
    turn_id: int,
    team_id: int,
    my_restaurant: dict[str, Any] | None,
    restaurants: list[dict[str, Any]] | None,
    recipes: list[dict[str, Any]] | None,
    meals: list[dict[str, Any]] | None,
    market_entries: list[dict[str, Any]] | None,
) -> DiscoveryContext:
    context = DiscoveryContext(turn_id=turn_id, recipient_id=team_id)

    if my_restaurant:
        context.turn_id = int(
            _first_present(
                my_restaurant,
                ["currentTurnId", "turn_id", "turnId", "activeTurnId"],
                context.turn_id,
            )
        )

    if recipes:
        first_recipe = recipes[0]
        context.dish_name = str(
            _first_present(first_recipe, ["name", "dish", "dishName"], context.dish_name)
        )
        ingredients = first_recipe.get("ingredients")
        if isinstance(ingredients, list) and ingredients:
            first_ingredient = ingredients[0]
            if isinstance(first_ingredient, str):
                context.ingredient_name = first_ingredient
            elif isinstance(first_ingredient, dict):
                context.ingredient_name = str(
                    _first_present(
                        first_ingredient,
                        ["ingredient", "ingredient_name", "name"],
                        context.ingredient_name,
                    )
                )

    if meals:
        first_meal = meals[0]
        context.client_id = str(
            _first_present(first_meal, ["clientId", "client_id", "id"], context.client_id)
        )

    if market_entries:
        first_entry = market_entries[0]
        entry_id = _first_present(first_entry, ["id", "marketEntryId", "entry_id"], -1)
        try:
            context.market_entry_id = int(entry_id)
        except (TypeError, ValueError):
            context.market_entry_id = -1

    if restaurants:
        for restaurant in restaurants:
            restaurant_id = _first_present(restaurant, ["id", "restaurant_id", "team_id"], None)
            try:
                parsed_id = int(restaurant_id)
            except (TypeError, ValueError):
                continue
            if parsed_id != team_id:
                context.recipient_id = parsed_id
                break

    return context


DISCOVERY_ENDPOINTS: list[str] = [
    "get_my_restaurant",
    "get_restaurants",
    "get_recipes",
    "get_my_menu",
    "get_market_entries",
    "get_meals",
    "get_bid_history",
    "submit_closed_bids",
    "save_menu",
    "create_market_entry",
    "execute_transaction",
    "delete_market_entry",
    "prepare_dish",
    "serve_dish",
    "set_restaurant_open_status",
    "send_direct_message",
]


@asynccontextmanager
async def _managed_session(client: HackapizzaClient, enabled: bool = True):
    """Attach a temporary aiohttp session if needed."""
    if not enabled or client._session is not None:
        yield
        return

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=client._headers) as session:
        client._session = session
        try:
            yield
        finally:
            client._session = None


async def _invoke_endpoint(
    endpoint: str,
    call: Callable[[], Awaitable[Any]],
    args: dict[str, Any],
    logger: logging.Logger,
) -> EndpointCallResult:
    started = perf_counter()
    try:
        value = await call()
        duration_ms = (perf_counter() - started) * 1000
        logger.info("[OK] %s (%0.1f ms)", endpoint, duration_ms)
        return EndpointCallResult(
            endpoint=endpoint,
            status="ok",
            duration_ms=duration_ms,
            args=_safe_json(args),
            result=_safe_json(value),
        )
    except Exception as exc:  # noqa: BLE001 - we intentionally collect every failure
        duration_ms = (perf_counter() - started) * 1000
        logger.exception("[ERROR] %s (%0.1f ms): %s", endpoint, duration_ms, exc)
        return EndpointCallResult(
            endpoint=endpoint,
            status="error",
            duration_ms=duration_ms,
            args=_safe_json(args),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


async def run_discovery(
    client: HackapizzaClient,
    output_dir: str | Path = "artifacts/api_discovery",
    include_actions: bool = False,
    manage_session: bool = True,
    logger: logging.Logger | None = None,
    turn_id: int = 7,
) -> Path:
    """Call each endpoint, collect result/error, and persist the report to disk."""
    logger = logger or LOGGER
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    async with _managed_session(client, enabled=manage_session):
        results: list[EndpointCallResult] = []

        my_restaurant_res: EndpointCallResult | None = None
        restaurants_res: EndpointCallResult | None = None
        recipes_res: EndpointCallResult | None = None
        my_menu_res: EndpointCallResult | None = None
        market_entries_res: EndpointCallResult | None = None

        # Data endpoints first, so we can infer realistic payloads for action endpoints.
        if "get_my_restaurant" in DISCOVERY_ENDPOINTS:
            my_restaurant_res = await _invoke_endpoint(
                "get_my_restaurant", client.get_my_restaurant, {}, logger
            )
            results.append(my_restaurant_res)

        if "get_restaurants" in DISCOVERY_ENDPOINTS:
            restaurants_res = await _invoke_endpoint(
                "get_restaurants", client.get_restaurants, {}, logger
            )
            results.append(restaurants_res)

        if "get_recipes" in DISCOVERY_ENDPOINTS:
            recipes_res = await _invoke_endpoint("get_recipes", client.get_recipes, {}, logger)
            results.append(recipes_res)

        if "get_my_menu" in DISCOVERY_ENDPOINTS:
            my_menu_res = await _invoke_endpoint("get_my_menu", client.get_my_menu, {}, logger)
            results.append(my_menu_res)

        if "get_market_entries" in DISCOVERY_ENDPOINTS:
            market_entries_res = await _invoke_endpoint(
                "get_market_entries", client.get_market_entries, {}, logger
            )
            results.append(market_entries_res)

        context = _build_context_from_snapshots(
            turn_id=turn_id,
            team_id=client.team_id,
            my_restaurant=my_restaurant_res.result
            if my_restaurant_res and isinstance(my_restaurant_res.result, dict)
            else None,
            restaurants=restaurants_res.result
            if restaurants_res and isinstance(restaurants_res.result, list)
            else None,
            recipes=recipes_res.result if recipes_res and isinstance(recipes_res.result, list) else None,
            meals=None,
            market_entries=(
                market_entries_res.result if market_entries_res and isinstance(market_entries_res.result, list) else None
            ),
        )

        if "get_meals" in DISCOVERY_ENDPOINTS:
            meals_res = await _invoke_endpoint(
                "get_meals",
                lambda: client.get_meals(context.turn_id),
                {"turn_id": context.turn_id},
                logger,
            )
            results.append(meals_res)
            if isinstance(meals_res.result, list):
                context = _build_context_from_snapshots(
                    turn_id=context.turn_id,
                    team_id=client.team_id,
                    my_restaurant=my_restaurant_res.result
                    if my_restaurant_res and isinstance(my_restaurant_res.result, dict)
                    else None,
                    restaurants=restaurants_res.result
                    if restaurants_res and isinstance(restaurants_res.result, list)
                    else None,
                    recipes=recipes_res.result if recipes_res and isinstance(recipes_res.result, list) else None,
                    meals=meals_res.result,
                    market_entries=market_entries_res.result
                    if market_entries_res and isinstance(market_entries_res.result, list)
                    else None,
                )

        if "get_bid_history" in DISCOVERY_ENDPOINTS:
            bid_history_res = await _invoke_endpoint(
                "get_bid_history",
                lambda: client.get_bid_history(context.turn_id),
                {"turn_id": context.turn_id},
                logger,
            )
            results.append(bid_history_res)

        if include_actions:
            bids = [BidRequest(ingredient=context.ingredient_name, bid=1.0, quantity=1)]
            menu = [MenuItem(name=context.dish_name, price=9.99)]

            if "submit_closed_bids" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "submit_closed_bids",
                        lambda: client.submit_closed_bids(bids),
                        {"bids": [asdict(b) for b in bids]},
                        logger,
                    )
                )

            if "save_menu" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "save_menu",
                        lambda: client.save_menu(menu),
                        {"items": [asdict(item) for item in menu]},
                        logger,
                    )
                )

            if "create_market_entry" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "create_market_entry",
                        lambda: client.create_market_entry(
                            MarketSide.BUY,
                            context.ingredient_name,
                            1,
                            1.0,
                        ),
                        {
                            "side": MarketSide.BUY.value,
                            "ingredient_name": context.ingredient_name,
                            "quantity": 1,
                            "price": 1.0,
                        },
                        logger,
                    )
                )

            if "execute_transaction" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "execute_transaction",
                        lambda: client.execute_transaction(context.market_entry_id),
                        {"market_entry_id": context.market_entry_id},
                        logger,
                    )
                )

            if "delete_market_entry" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "delete_market_entry",
                        lambda: client.delete_market_entry(context.market_entry_id),
                        {"market_entry_id": context.market_entry_id},
                        logger,
                    )
                )

            if "prepare_dish" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "prepare_dish",
                        lambda: client.prepare_dish(context.dish_name),
                        {"dish_name": context.dish_name},
                        logger,
                    )
                )

            if "serve_dish" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "serve_dish",
                        lambda: client.serve_dish(context.dish_name, context.client_id),
                        {"dish_name": context.dish_name, "client_id": context.client_id},
                        logger,
                    )
                )

            if "set_restaurant_open_status" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "set_restaurant_open_status",
                        lambda: client.set_restaurant_open_status(True),
                        {"is_open": True},
                        logger,
                    )
                )

            if "send_direct_message" in DISCOVERY_ENDPOINTS:
                results.append(
                    await _invoke_endpoint(
                        "send_direct_message",
                        lambda: client.send_direct_message(
                            context.recipient_id,
                            "Automated endpoint discovery ping.",
                        ),
                        {
                            "recipient_id": context.recipient_id,
                            "text": "Automated endpoint discovery ping.",
                        },
                        logger,
                    )
                )

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at": now,
        "team_id": client.team_id,
        "base_url": client.base_url,
        "include_actions": include_actions,
        "results": [asdict(item) for item in results],
        "summary": {
            "total": len(results),
            "ok": sum(item.status == "ok" for item in results),
            "error": sum(item.status == "error" for item in results),
        },
    }

    report_path = output / f"discovery_report_{now}.json"
    latest_path = output / "latest.json"

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Discovery report written to %s", report_path)
    return report_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Hackapizza endpoint discovery")
    parser.add_argument("--team-id", type=int, default=None)
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--base-url", type=str, default="https://hackapizza.datapizza.tech")
    parser.add_argument("--output-dir", type=str, default="artifacts/api_discovery")
    parser.add_argument(
        "--include-actions",
        action="store_true",
        help="Include mutating action endpoints in discovery.",
    )
    parser.add_argument("--log-level", type=str, default="INFO")
    parser.add_argument("--turn-id", type=int, default=7, help="Turn ID to use for context when calling endpoints.")
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    client = HackapizzaClient(
        team_id=args.team_id,
        api_key=args.api_key,
        base_url=args.base_url,
    )
    report = await run_discovery(
        client,
        output_dir=args.output_dir,
        include_actions=args.include_actions,
        manage_session=True,
        logger=LOGGER,
    )
    print(f"Discovery completed. Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
