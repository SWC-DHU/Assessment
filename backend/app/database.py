from collections.abc import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


class Base(DeclarativeBase):
    """Base SQLAlchemy declarative class."""


def ensure_schema_migrations(target_engine: Engine = engine) -> None:
    """Idempotent, backward-compatible SQLite column migrations preserving existing data."""
    if not str(target_engine.url).startswith("sqlite"):
        return
    with target_engine.begin() as conn:
        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }
        if "agent_profiles" in tables:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(agent_profiles)")).fetchall()}
            if "agent_type" not in cols:
                conn.execute(
                    text("ALTER TABLE agent_profiles ADD COLUMN agent_type VARCHAR(40) NOT NULL DEFAULT 'requirement_parsing'")
                )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_profiles_agent_type ON agent_profiles (agent_type)"))
        if "resource_matching_runs" in tables:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(resource_matching_runs)")).fetchall()}
            migrations = [
                ("agent_id", "ALTER TABLE resource_matching_runs ADD COLUMN agent_id VARCHAR(36)"),
                ("agent_name", "ALTER TABLE resource_matching_runs ADD COLUMN agent_name VARCHAR(120)"),
                ("agent_type", "ALTER TABLE resource_matching_runs ADD COLUMN agent_type VARCHAR(40) NOT NULL DEFAULT 'resource_matching'"),
                ("llm_mode", "ALTER TABLE resource_matching_runs ADD COLUMN llm_mode VARCHAR(24) NOT NULL DEFAULT 'disabled'"),
            ]
            for col_name, ddl in migrations:
                if col_name not in cols:
                    conn.execute(text(ddl))


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and ensure closure after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
