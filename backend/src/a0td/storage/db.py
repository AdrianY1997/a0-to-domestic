"""SQLite access (SPEC 11.1): one engine per app, WAL, foreign keys, automatic migrations."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
    finally:
        cur.close()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def make_engine(path: Path) -> Engine:
    engine = create_engine(
        sqlite_url(path),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(engine, "connect", _set_pragmas)
    return engine


def run_migrations(path: Path) -> None:
    """``alembic upgrade head`` against ``path`` (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(path).replace("%", "%%"))
    command.upgrade(cfg, "head")


class Database:
    """Engine + session factory. Cheap to construct: nothing touches the disk until used."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine = make_engine(path)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        run_migrations(self.path)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A session that commits on success and rolls back on error."""
        with self._factory() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def check(self) -> bool:
        """True if the database answers a trivial query."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    def dispose(self) -> None:
        self.engine.dispose()
