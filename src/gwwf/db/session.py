"""SQLAlchemy engine + session factory for the gridworks-weather DB.

`session_factory_from` builds from an explicit settings object (the
actor's own); the module-level `SessionLocal` reads env-configured
`GwwfSettings` lazily on first use for CLI/one-off work.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from gwwf.config import GwwfSettings


def session_factory_from(settings: GwwfSettings) -> sessionmaker[Session]:
    engine = create_engine(
        settings.db_url.get_secret_value(),
        echo=settings.db_echo,
        future=True,
    )
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return session_factory_from(GwwfSettings())


def SessionLocal() -> Session:
    """Open a session on the env-configured engine (built on first call)."""
    return _session_factory()()
