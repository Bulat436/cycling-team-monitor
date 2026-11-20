"""Stage A access and audit tables."""

from alembic import op
import sqlalchemy as sa

revision = "0001_access"
down_revision = None
branch_labels = None
depends_on = None


def timestamps():
    return (
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(7), nullable=False),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("activated_at", sa.Integer()),
        *timestamps(),
        sa.CheckConstraint("role IN ('athlete', 'coach')"),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issued_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.Integer()),
        sa.Column("revoked_at", sa.Integer()),
        *timestamps(),
        sa.CheckConstraint("kind IN ('activation', 'reset')"),
    )
    op.create_index("ix_invitations_user_id", "invitations", ["user_id"])
    op.create_table(
        "login_limits",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_login_limits_expires_at", "login_limits", ["expires_at"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(40), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_target_id", "audit_logs", ["target_id"])


def downgrade() -> None:
    for table in ("audit_logs", "login_limits", "invitations", "auth_sessions", "users"):
        op.drop_table(table)
