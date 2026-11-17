from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, LargeBinary, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLE_ADMIN = "admin"
ROLE_USER = "user"


class Base(DeclarativeBase):
    pass


class User(Base):
    """Compte local. Le rôle vit ici, en base — jamais déduit d'un token."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(20))
    is_bootstrap: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Il ne peut jamais exister deux administrateurs issus de l'écran de
    # bootstrap : l'unicité est tranchée par la base, pas par une lecture
    # préalable (deux créations concurrentes passeraient toutes les deux).
    __table_args__ = (
        Index(
            "uq_users_bootstrap_admin",
            "is_bootstrap",
            unique=True,
            postgresql_where=text("is_bootstrap"),
            sqlite_where=text("is_bootstrap"),
        ),
    )


class Secret(Base):
    """Secret de l'application. La valeur ne vit qu'ici, chiffrée — pas de
    coffre externe, pas de valeur déportée dans un fichier (décision Gael)."""

    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
