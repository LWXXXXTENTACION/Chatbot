"""Idempotent Alembic migration entry point, including legacy DB adoption."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import DATABASE_URL

BASELINE_REVISION = "0001_initial"
LEGACY_TABLES = {"users", "conversations", "messages", "message_parts"}


def _alembic_config(database_url: str) -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("+aiosqlite", ""))
    return config


def run_migrations(database_url: str | None = None) -> None:
    """Adopt the pre-Alembic schema if present, then upgrade to head."""
    effective_url = database_url or DATABASE_URL
    config = _alembic_config(effective_url)
    sync_url = effective_url.replace("+aiosqlite", "")
    engine = create_engine(sync_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    if LEGACY_TABLES.issubset(tables) and "alembic_version" not in tables:
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


if __name__ == "__main__":
    run_migrations()
