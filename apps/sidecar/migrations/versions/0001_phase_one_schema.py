"""Create the Phase 1 local persistence schema."""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from sentia_sidecar.models import Base

    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    from sentia_sidecar.models import Base

    Base.metadata.drop_all(bind=bind)
