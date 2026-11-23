"""Add the uploaded duration to the shared sleep diary."""

import sqlalchemy as sa
from alembic import op

revision = "0011_sleep_report_diary"
down_revision = "0010_private_sleep_reports"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sleep_days") as batch:
        batch.add_column(sa.Column("report_minutes", sa.Integer(), nullable=True))
        batch.create_check_constraint("ck_sleep_report_minutes", "report_minutes BETWEEN 0 AND 1440")
        batch.drop_constraint("ck_sleep_selected_source", type_="check")
        batch.alter_column("selected_source", type_=sa.String(9), existing_type=sa.String(8))
        batch.create_check_constraint("ck_sleep_selected_source", "selected_source IN ('manual', 'legacy', 'automatic')")


def downgrade():
    op.execute("UPDATE sleep_days SET selected_source = NULL WHERE selected_source = 'automatic'")
    op.execute("""
        UPDATE wellness_entries AS w SET sleep_minutes = (
            SELECT CASE WHEN s.duration_cleared THEN NULL
                        WHEN s.selected_source = 'legacy' THEN s.legacy_minutes
                        ELSE COALESCE(s.manual_minutes, s.calculated_minutes, s.legacy_minutes) END
            FROM sleep_days AS s WHERE s.athlete_id = w.athlete_id AND s.sleep_date = w.entry_date
        ) WHERE EXISTS (SELECT 1 FROM sleep_days AS s WHERE s.athlete_id = w.athlete_id
                         AND s.sleep_date = w.entry_date AND s.report_minutes IS NOT NULL)
    """)
    with op.batch_alter_table("sleep_days") as batch:
        batch.drop_constraint("ck_sleep_report_minutes", type_="check")
        batch.drop_constraint("ck_sleep_selected_source", type_="check")
        batch.drop_column("report_minutes")
        batch.alter_column("selected_source", type_=sa.String(8), existing_type=sa.String(9))
        batch.create_check_constraint("ck_sleep_selected_source", "selected_source IN ('manual', 'legacy')")
