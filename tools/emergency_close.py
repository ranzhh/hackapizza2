"""Emergency utility to force-close the current team's restaurant.

Usage:
  uv run python tools/emergency_close.py
  uv run python tools/emergency_close.py --team-id 12 --api-key xxx
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import aiohttp
from pydantic import BaseModel, Field

from hp2.core.api import HackapizzaClient


class EmergencyCloseResult(BaseModel):
    """Typed result returned by the emergency close flow."""

    team_id: int = Field(description="Team identifier used for the operation")
    was_open: bool = Field(description="Restaurant open status before the emergency close call")
    is_open: bool = Field(description="Restaurant open status after the emergency close call")
    action_response: dict[str, Any] | None = Field(
        default=None,
        description="Raw MCP response from update_restaurant_is_open",
    )


async def emergency_close_restaurant(client: HackapizzaClient) -> EmergencyCloseResult:
    """Force-close restaurant via MCP and verify final status.

    This function always calls ``set_restaurant_open_status(False)`` as requested,
    then re-reads restaurant state to confirm the final status.
    """

    status_before = await client.get_my_restaurant()
    action_response_raw = await client.set_restaurant_open_status(False)
    status_after = await client.get_my_restaurant()

    action_response: dict[str, Any] | None = (
        action_response_raw if isinstance(action_response_raw, dict) else None
    )

    return EmergencyCloseResult(
        team_id=client.team_id,
        was_open=bool(status_before.is_open),
        is_open=bool(status_after.is_open),
        action_response=action_response,
    )


async def run_emergency_close(
    *,
    team_id: int | None,
    api_key: str | None,
    base_url: str,
    timeout_seconds: float,
) -> EmergencyCloseResult:
    """Create client/session, execute emergency close, and return typed result."""

    client = HackapizzaClient(
        team_id=team_id,
        api_key=api_key,
        base_url=base_url,
    )

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout, headers=client._headers) as session:
        client._session = session
        return await emergency_close_restaurant(client)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emergency close for Hackapizza restaurant")
    parser.add_argument("--team-id", type=int, default=None, help="Override team id")
    parser.add_argument("--api-key", type=str, default=None, help="Override API key")
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://hackapizza.datapizza.tech",
        help="Hackapizza API base URL",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    result = await run_emergency_close(
        team_id=args.team_id,
        api_key=args.api_key,
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
    )

    print(result.model_dump_json(indent=2))
    if result.is_open:
        print("WARNING: restaurant is still open after emergency close attempt")
        return 1

    print("Emergency close completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
