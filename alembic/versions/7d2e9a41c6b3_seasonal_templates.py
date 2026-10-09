"""seasonal templates

Revision ID: 7d2e9a41c6b3
Revises: ca54084bad51
Create Date: 2026-10-07 18:40:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7d2e9a41c6b3"
down_revision: Union[str, Sequence[str], None] = "ca54084bad51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """One gw.weather.seasonal.template.gt record per row."""
    op.create_table(
        "seasonal_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("location_alias", sa.String(), nullable=False),
        sa.Column(
            "temp_by_month", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_alias"],
            ["locations.alias"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_alias", "start"),
    )


def downgrade() -> None:
    op.drop_table("seasonal_templates")
