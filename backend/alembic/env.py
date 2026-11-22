from alembic import context

# Register the domain tables in Base.metadata.
from app import analytics_models, athlete_models, garmin_models, onelap_models, sleep_models, workout_models
from app.config import Settings
from app.database import make_engine
from app.models import Base

settings = context.config.attributes.get("settings") or Settings()
options = {"target_metadata": Base.metadata, "render_as_batch": True}

if context.is_offline_mode():
    context.configure(dialect_name="sqlite", literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(settings)
    try:
        with engine.connect() as connection:
            mode = connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
            if mode.lower() != "wal":
                raise RuntimeError("SQLite WAL is unavailable")
            connection.commit()
            context.configure(connection=connection, transactional_ddl=True, **options)
            with context.begin_transaction():
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                context.run_migrations()
    finally:
        engine.dispose()
