"""Record which reports were submitted with team visibility."""

import sqlalchemy as sa
from alembic import op

revision = "0012_team_sleep_reports"
down_revision = "0011_sleep_report_diary"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("private_sleep_report_nights", sa.Column("team_visible", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("private_sleep_report_nights") as batch:
        batch.drop_column("team_visible")
