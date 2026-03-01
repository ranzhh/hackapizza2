from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from hp2.core.schema.models import MarketEntrySchema, MealSchema, RecipeSchema, RestaurantSchema


class Base(DeclarativeBase):
    pass


class CallLog(Base):
    __tablename__ = "calls"
    __table_args__ = (
        Index("idx_calls_timestamp", "timestamp_utc"),
        Index("idx_calls_name", "name"),
        Index("idx_calls_turn_id", "turn_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    turn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class RecipeLog(Base):
    __tablename__ = "recipes"
    __table_args__ = (Index("idx_recipes_call_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    preparation_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prestige: Mapped[int] = mapped_column(Integer, nullable=False)


class RecipeIngredientLog(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (Index("idx_recipe_ingredients_recipe_id", "recipe_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_name: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)


class RestaurantLog(Base):
    __tablename__ = "restaurants"
    __table_args__ = (Index("idx_restaurants_call_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    restaurant_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    reputation: Mapped[float] = mapped_column(Float, nullable=False)
    is_open: Mapped[bool] = mapped_column(Integer, nullable=False)


class RestaurantInventoryLog(Base):
    __tablename__ = "restaurant_inventory"
    __table_args__ = (Index("idx_restaurant_inventory_restaurant_row_id", "restaurant_row_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_row_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_name: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)


class RestaurantMenuItemLog(Base):
    __tablename__ = "restaurant_menu_items"
    __table_args__ = (Index("idx_restaurant_menu_items_restaurant_row_id", "restaurant_row_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_row_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class MealLog(Base):
    __tablename__ = "meals"
    __table_args__ = (Index("idx_meals_call_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(String, nullable=False)


class MarketEntryLog(Base):
    __tablename__ = "market_entries"
    __table_args__ = (Index("idx_market_entries_call_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(String, nullable=False)


class BidHistoryLog(Base):
    __tablename__ = "bid_history"
    __table_args__ = (Index("idx_bid_history_call_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(String, nullable=False)


# ---------------------------------------------------------------------------
# Shared event log table (mirrors the event_logger service's `events` table)
# ---------------------------------------------------------------------------


class MixinEventLog(Base):
    """ORM mapping to the shared `events` table owned by the event_logger service."""

    __tablename__ = "events"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    turn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# Typed MCP action event tables
# ---------------------------------------------------------------------------


class McpClosedBidEvent(Base):
    __tablename__ = "event_mcp_closed_bid"
    __table_args__ = (Index("idx_mcp_closed_bid_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    bids_json: Mapped[str] = mapped_column(String, nullable=False)


class McpSaveMenuEvent(Base):
    __tablename__ = "event_mcp_save_menu"
    __table_args__ = (Index("idx_mcp_save_menu_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    items_json: Mapped[str] = mapped_column(String, nullable=False)


class McpCreateMarketEntryEvent(Base):
    __tablename__ = "event_mcp_create_market_entry"
    __table_args__ = (Index("idx_mcp_create_market_entry_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String, nullable=False)
    ingredient_name: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)


class McpExecuteTransactionEvent(Base):
    __tablename__ = "event_mcp_execute_transaction"
    __table_args__ = (Index("idx_mcp_execute_transaction_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    market_entry_id: Mapped[int] = mapped_column(Integer, nullable=False)


class McpDeleteMarketEntryEvent(Base):
    __tablename__ = "event_mcp_delete_market_entry"
    __table_args__ = (Index("idx_mcp_delete_market_entry_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    market_entry_id: Mapped[int] = mapped_column(Integer, nullable=False)


class McpPrepareDishEvent(Base):
    __tablename__ = "event_mcp_prepare_dish"
    __table_args__ = (Index("idx_mcp_prepare_dish_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    dish_name: Mapped[str] = mapped_column(String, nullable=False)


class McpServeDishEvent(Base):
    __tablename__ = "event_mcp_serve_dish"
    __table_args__ = (Index("idx_mcp_serve_dish_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    dish_name: Mapped[str] = mapped_column(String, nullable=False)
    client_id: Mapped[str] = mapped_column(String, nullable=False)


class McpSetOpenStatusEvent(Base):
    __tablename__ = "event_mcp_set_open_status"
    __table_args__ = (Index("idx_mcp_set_open_status_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    is_open: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=open, 0=closed


class McpSendMessageEvent(Base):
    __tablename__ = "event_mcp_send_message"
    __table_args__ = (Index("idx_mcp_send_message_event_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)


class IngredientsBidSuggestion(Base):
    __tablename__ = "ingredients_bid_suggestion"

    turn_id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class RecipesPriceSuggestion(Base):
    __tablename__ = "recipes_price_suggestions"

    turn_id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class SqlLoggingMixin:
    """SQL-backed metadata logger for API/MCP calls."""

    def _init_sql_logging(self, connstr: str) -> None:
        self._log_connstr = connstr
        self._log_engine = create_engine(connstr, future=True)

        if self._log_engine.dialect.name == "sqlite":

            @event.listens_for(self._log_engine, "connect")
            def _set_sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.close()

        Base.metadata.create_all(self._log_engine)
        self._log_session_factory = sessionmaker(
            bind=self._log_engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

    def _close_sql_logging(self) -> None:
        engine = getattr(self, "_log_engine", None)
        if engine is not None:
            engine.dispose()
            self._log_engine = None
            self._log_session_factory = None

    def _extract_turn_id_from_endpoint(self, endpoint: str) -> Optional[str]:
        query = urlparse(endpoint).query
        if not query:
            return None
        parsed = parse_qs(query)
        value = parsed.get("turn_id")
        if not value:
            return None
        return value[0]

    def _log_call_metadata(
        self,
        *,
        source: str,
        name: str,
        status: str,
        duration_ms: float | None = None,
        turn_id: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> Optional[int]:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return None

        with session_factory() as session:
            row = CallLog(
                timestamp_utc=datetime.now(UTC),
                turn_id=turn_id,
                source=source,
                name=name,
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
                error_message=error_message,
            )
            session.add(row)
            session.flush()
            call_id = row.id
            session.commit()
            return call_id

    def _persist_recipes(self, *, call_id: int, recipes: list[RecipeSchema]) -> None:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        with session_factory() as session:
            for recipe in recipes:
                recipe_row = RecipeLog(
                    call_id=call_id,
                    name=recipe.name,
                    preparation_time_ms=recipe.preparation_time_ms,
                    prestige=recipe.prestige,
                )
                session.add(recipe_row)
                session.flush()

                for ingredient_name, quantity in recipe.ingredients.items():
                    session.add(
                        RecipeIngredientLog(
                            recipe_id=recipe_row.id,
                            ingredient_name=ingredient_name,
                            quantity=quantity,
                        )
                    )

            session.commit()

    def _persist_restaurants(self, *, call_id: int, restaurants: list[RestaurantSchema]) -> None:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        with session_factory() as session:
            for restaurant in restaurants:
                restaurant_row = RestaurantLog(
                    call_id=call_id,
                    restaurant_id=restaurant.id,
                    name=restaurant.name,
                    balance=restaurant.balance,
                    reputation=restaurant.reputation,
                    is_open=1 if restaurant.is_open else 0,
                )
                session.add(restaurant_row)
                session.flush()

                for ingredient_name, quantity in restaurant.inventory.items():
                    session.add(
                        RestaurantInventoryLog(
                            restaurant_row_id=restaurant_row.id,
                            ingredient_name=ingredient_name,
                            quantity=float(quantity),
                        )
                    )

                for menu_item in restaurant.menu.items:
                    session.add(
                        RestaurantMenuItemLog(
                            restaurant_row_id=restaurant_row.id,
                            name=menu_item.name,
                            price=menu_item.price,
                        )
                    )

            session.commit()

    def _persist_meals(self, *, call_id: int, meals: list[MealSchema]) -> None:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        with session_factory() as session:
            for meal in meals:
                session.add(
                    MealLog(
                        call_id=call_id,
                        payload_json=json.dumps(
                            meal.model_dump(by_alias=True), ensure_ascii=False
                        ),
                    )
                )
            session.commit()

    def _persist_market_entries(self, *, call_id: int, entries: list[MarketEntrySchema]) -> None:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        with session_factory() as session:
            for entry in entries:
                session.add(
                    MarketEntryLog(
                        call_id=call_id,
                        payload_json=json.dumps(
                            entry.model_dump(by_alias=True), ensure_ascii=False
                        ),
                    )
                )
            session.commit()

    def _persist_bid_history(self, *, call_id: int, bids: list[Any]) -> None:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        with session_factory() as session:
            for bid in bids:
                session.add(
                    BidHistoryLog(
                        call_id=call_id,
                        payload_json=json.dumps(bid.model_dump(by_alias=True), ensure_ascii=False),
                    )
                )
            session.commit()

    # --- MCP Action Event Logging ---

    def _log_mcp_event(
        self,
        *,
        event_type: str,
        persist_method_name: str,
        data: dict[str, Any],
    ) -> None:
        """Write a self-generated MCP action to `events` + its typed table."""
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        turn_id: Optional[str] = getattr(self, "_current_turn_id", None)
        _logger = getattr(self, "logger", logging.getLogger(__name__))

        try:
            with session_factory() as session:
                event_row = MixinEventLog(
                    timestamp_utc=datetime.now(UTC),
                    turn_id=turn_id,
                    event_type=event_type,
                    data_json=None,
                )
                session.add(event_row)
                session.flush()
                event_id = event_row.id

                persist_method = getattr(self, persist_method_name, None)
                if callable(persist_method):
                    persist_method(session=session, event_id=event_id, data=data)

                session.commit()
        except Exception as e:
            _logger.debug("MCP event logging failed: %s", e, exc_info=True)

    def _persist_mcp_closed_bid(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpClosedBidEvent(
                event_id=event_id,
                bids_json=json.dumps([asdict(b) for b in data.get("bids", [])], default=str),
            )
        )

    def _persist_mcp_save_menu(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpSaveMenuEvent(
                event_id=event_id,
                items_json=json.dumps([asdict(i) for i in data.get("items", [])], default=str),
            )
        )

    def _persist_mcp_create_market_entry(self, *, session, event_id: int, data: dict) -> None:
        side = data.get("side", "")
        session.add(
            McpCreateMarketEntryEvent(
                event_id=event_id,
                side=side.value if hasattr(side, "value") else str(side),
                ingredient_name=data.get("ingredient_name", ""),
                quantity=data.get("quantity", 0),
                price=data.get("price", 0.0),
            )
        )

    def _persist_mcp_execute_transaction(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpExecuteTransactionEvent(
                event_id=event_id,
                market_entry_id=data.get("market_entry_id", 0),
            )
        )

    def _persist_mcp_delete_market_entry(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpDeleteMarketEntryEvent(
                event_id=event_id,
                market_entry_id=data.get("market_entry_id", 0),
            )
        )

    def _persist_mcp_prepare_dish(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpPrepareDishEvent(
                event_id=event_id,
                dish_name=data.get("dish_name", ""),
            )
        )

    def _persist_mcp_serve_dish(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpServeDishEvent(
                event_id=event_id,
                dish_name=data.get("dish_name", ""),
                client_id=data.get("client_id", ""),
            )
        )

    def _persist_mcp_set_open_status(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpSetOpenStatusEvent(
                event_id=event_id,
                is_open=1 if data.get("is_open") else 0,
            )
        )

    def _persist_mcp_send_message(self, *, session, event_id: int, data: dict) -> None:
        session.add(
            McpSendMessageEvent(
                event_id=event_id,
                recipient_id=data.get("recipient_id", 0),
                text=data.get("text", ""),
            )
        )
