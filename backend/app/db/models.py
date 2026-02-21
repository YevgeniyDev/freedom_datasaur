# backend/app/db/models.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    String,
    Integer,
    Date,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# -----------------------------
# Business Units (Offices)
# -----------------------------
class BusinessUnit(Base):
    __tablename__ = "business_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    office_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Optional: if you want to store normalized office coordinates
    lat: Mapped[Optional[float]] = mapped_column(nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(nullable=True)

    managers: Mapped[List["Manager"]] = relationship(back_populates="business_unit", cascade="all, delete-orphan")
    assignments: Mapped[List["Assignment"]] = relationship(back_populates="business_unit")


# -----------------------------
# Managers
# -----------------------------
class Manager(Base):
    __tablename__ = "managers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str] = mapped_column(String(100), nullable=False)  # "Спец", "Ведущий спец", "Глав спец"
    # Prefer ARRAY for simple skills like ["VIP", "ENG", "KZ"]
    skills: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    business_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_units.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # From CSV (initial load); your effective load can be computed using assignments
    current_load: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    business_unit: Mapped["BusinessUnit"] = relationship(back_populates="managers")
    assignments: Mapped[List["Assignment"]] = relationship(back_populates="manager")

    __table_args__ = (
        Index("ix_managers_bu_id", "business_unit_id"),
    )


# -----------------------------
# Tickets
# -----------------------------
class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    client_guid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    birth_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)

    segment: Mapped[str] = mapped_column(String(32), nullable=False)  # Mass, VIP, Priority

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachment_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    street: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    house: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    ai: Mapped[Optional["TicketAI"]] = relationship(
        back_populates="ticket",
        uselist=False,
        cascade="all, delete-orphan",
    )
    assignment: Mapped[Optional["Assignment"]] = relationship(
        back_populates="ticket",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_tickets_segment", "segment"),
        Index("ix_tickets_city", "city"),
    )


# -----------------------------
# Ticket AI Analytics
# -----------------------------
class TicketAI(Base):
    __tablename__ = "ticket_ai"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Key requirement: FK -> tickets.id
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 1-to-1: one AI row per ticket
    )

    # NLP outputs
    type_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # 7 categories
    sentiment: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)      # Позитивный/Нейтральный/Негативный
    urgency: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)           # 1..10
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="RU")   # RU/ENG/KZ

    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_actions: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)

    # Geo-normalized coords
    geo_lat: Mapped[Optional[float]] = mapped_column(nullable=True)
    geo_lon: Mapped[Optional[float]] = mapped_column(nullable=True)

    confidence: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    needs_review: Mapped[bool] = mapped_column(nullable=False, default=False)

    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    ticket: Mapped["Ticket"] = relationship(back_populates="ai")


# -----------------------------
# Assignments (Ticket -> Manager)
# -----------------------------
class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Key requirements: FKs
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 1 ticket -> 1 assignment
    )

    manager_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("managers.id", ondelete="RESTRICT"),
        nullable=False,
    )

    business_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_units.id", ondelete="RESTRICT"),
        nullable=False,
    )

    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Key requirement: decision_trace JSONB
    decision_trace: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="assignment")
    manager: Mapped["Manager"] = relationship(back_populates="assignments")
    business_unit: Mapped["BusinessUnit"] = relationship(back_populates="assignments")

    __table_args__ = (
        Index("ix_assignments_manager_id", "manager_id"),
        Index("ix_assignments_business_unit_id", "business_unit_id"),
    )


# -----------------------------
# Round Robin State
# -----------------------------
class RRState(Base):
    __tablename__ = "rr_state"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Key requirement: bucket_key unique
    bucket_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # last assigned manager for this bucket
    last_manager_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("managers.id", ondelete="SET NULL"),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("bucket_key", name="uq_rr_state_bucket_key"),
        Index("ix_rr_state_bucket_key", "bucket_key"),
    )
    