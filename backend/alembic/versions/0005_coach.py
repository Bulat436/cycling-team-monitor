"""Coach alert rules and team settings."""

import sqlalchemy as sa
from alembic import op

revision = "0005_coach"
down_revision = "0004_onelap"
branch_labels = None
depends_on = None


def timestamps():
    return (sa.Column("created_at", sa.Float(), nullable=False), sa.Column("updated_at", sa.Float(), nullable=False))


def upgrade():
    op.create_table(
        "alerts", sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False), *timestamps(),
    )
    op.create_table(
        "app_settings", sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False), *timestamps(),
    )


def downgrade():
    op.drop_table("app_settings")
    op.drop_table("alerts")
