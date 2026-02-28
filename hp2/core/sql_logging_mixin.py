from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


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
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
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


class SqlLoggingMixin:
    """SQLite-backed metadata logger for API/MCP calls."""

    def _init_sql_logging(self, db_path: str) -> None:
        self._log_db_path = db_path
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self._log_engine = create_engine(f"sqlite:///{path}", future=True)

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

    def _persist_recipes(self, *, call_id: int, recipes: list[Any]) -> None:
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
