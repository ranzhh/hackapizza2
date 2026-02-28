"""Pydantic schemas inferred from the discovery report payloads.

These models represent the structures observed in
``artifacts/api_discovery/discovery_report_*.json`` and can be used to
validate/parse future discovery runs in a typed way.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MenuItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    price: Optional[float] = None


class MenuSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: List[MenuItemSchema] = Field(default_factory=list)


class RestaurantSchema(BaseModel):
    """Observed response shape for get_my_restaurant/get_restaurants entries."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    balance: float
    inventory: Dict[str, float | int] = Field(default_factory=dict)
    reputation: float
    is_open: bool = Field(alias="isOpen")
    kitchen: List[Dict[str, Any]] = Field(default_factory=list)
    menu: MenuSchema
    received_messages: List[Dict[str, Any]] = Field(default_factory=list, alias="receivedMessages")


class RecipeSchema(BaseModel):
    """Observed shape for items returned by get_recipes."""

    model_config = ConfigDict(extra="allow")

    name: str
    preparation_time_ms: int = Field(alias="preparationTimeMs")
    ingredients: Dict[str, int]
    prestige: int


class MarketEntrySchema(BaseModel):
    """Observed/expected shape for market entries (currently sparse in report)."""

    model_config = ConfigDict(extra="allow")


class MealSchema(BaseModel):
    """Observed/expected shape for meals endpoint entries."""

    model_config = ConfigDict(extra="allow")


class BidHistoryEntrySchema(BaseModel):
    """Observed/expected shape for bid history entries."""

    model_config = ConfigDict(extra="allow")


class EndpointCallSchema(BaseModel):
    """Single endpoint invocation result captured by the discovery harness."""

    model_config = ConfigDict(extra="allow")

    endpoint: str
    status: Literal["ok", "error"]
    duration_ms: float
    args: Dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class DiscoverySummarySchema(BaseModel):
    total: int
    ok: int
    error: int


class DiscoveryReportSchema(BaseModel):
    """Top-level schema for discovery_report JSON artifacts."""

    model_config = ConfigDict(extra="allow")

    generated_at: str
    team_id: int
    base_url: str
    include_actions: bool
    results: List[EndpointCallSchema]
    summary: DiscoverySummarySchema


# Endpoint-specific aliases for convenience in consuming code
RestaurantsResponseSchema = List[RestaurantSchema]
RecipesResponseSchema = List[RecipeSchema]
MarketEntriesResponseSchema = List[MarketEntrySchema]
MealsResponseSchema = List[MealSchema]
BidHistoryResponseSchema = List[BidHistoryEntrySchema]
MyRestaurantResponseSchema = RestaurantSchema
MyMenuResponseSchema = MenuSchema
