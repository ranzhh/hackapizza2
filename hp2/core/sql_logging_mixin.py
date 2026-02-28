from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import DateTime, Float, Index, Integer, String, create_engine, event
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
    ) -> None:
        session_factory = getattr(self, "_log_session_factory", None)
        if session_factory is None:
            return

        with session_factory() as session:
            session.add(
                CallLog(
                    timestamp_utc=datetime.now(UTC),
                    turn_id=turn_id,
                    source=source,
                    name=name,
                    status=status,
                    duration_ms=duration_ms,
                    error_type=error_type,
                    error_message=error_message,
                )
            )
            session.commit()
