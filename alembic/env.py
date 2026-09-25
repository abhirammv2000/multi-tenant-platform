from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from shared.db import Base
#imported so Alembic's autogenerate can see them, even though nothing else here calls them directly
from control_plane.app.models.tenants import Tenant
from control_plane.app.models.api_keys import APIKey
from control_plane.app.models.build_jobs import BuildJob
from control_plane.app.models.deployments import Deployment
from control_plane.app.models.webhook_registrations import WebhookRegistration
from control_plane.app.models.webhook_deliveries import WebhookDelivery

from shared.config import DATABASE_URL_SYNC

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL_SYNC)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
