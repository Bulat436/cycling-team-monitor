"""Identify the head coach and retain dormant references after account removal."""

import sqlalchemy as sa
from alembic import op

revision = "0007_head_coach"
down_revision = "0006_training_intelligence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_head_coach", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("deleted_at", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE users SET is_head_coach = 1 WHERE id = (
            SELECT u.id FROM users AS u
            WHERE u.role = 'coach' AND u.active = 1 AND u.activated_at IS NOT NULL
            ORDER BY NOT EXISTS (
                SELECT 1 FROM audit_logs AS a
                WHERE a.action = 'coach_bootstrapped' AND a.target_id = u.id
            ), u.id
            LIMIT 1
        )
    """)


def downgrade():
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "is_head_coach")
