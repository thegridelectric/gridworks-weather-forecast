import sys
from logging.config import fileConfig

# Add project root to sys.path
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

root_path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_path))

from gwwf.db.models import Base  # SQLAlchemy metadata
from gwwf.config import GwwfSettings

# -----------------------------------------------------------------------------
# Alembic Config
# -----------------------------------------------------------------------------
config = context.config

# Interpret config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# -----------------------------------------------------------------------------
# Database URL (from our pydantic Settings)
# -----------------------------------------------------------------------------
settings = GwwfSettings()
db_url = settings.db_url.get_secret_value()
config.set_main_option("sqlalchemy.url", db_url)


# -----------------------------------------------------------------------------
# Run migrations
# -----------------------------------------------------------------------------
def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
