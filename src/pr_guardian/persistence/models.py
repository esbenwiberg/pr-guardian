"""SQLAlchemy ORM models for review persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ReviewRow(Base):
    """A completed (or in-progress) PR review."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pr_id: Mapped[str] = mapped_column(String(64), index=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    author: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    source_branch: Mapped[str] = mapped_column(String(256), default="")
    target_branch: Mapped[str] = mapped_column(String(256), default="")
    head_commit_sha: Mapped[str] = mapped_column(String(64), default="")
    pr_url: Mapped[str] = mapped_column(Text, default="")

    risk_tier: Mapped[str] = mapped_column(String(16), default="")
    repo_risk_class: Mapped[str] = mapped_column(String(16), default="standard")
    trust_tier: Mapped[str] = mapped_column(String(32), default="")
    trust_tier_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    combined_score: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(String(32), default="pending")

    mechanical_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    override_reasons: Mapped[dict | list] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")

    # Pipeline stage tracking for live progress
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    stage_detail: Mapped[str] = mapped_column(Text, default="")
    pipeline_log: Mapped[list] = mapped_column(JSONB, default=list)

    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    mechanical_results: Mapped[list[MechanicalResultRow]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )
    agent_results: Mapped[list[AgentResultRow]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )


class MechanicalResultRow(Base):
    __tablename__ = "mechanical_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE")
    )
    tool: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    findings: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    review: Mapped[ReviewRow] = relationship(back_populates="mechanical_results")


class AgentResultRow(Base):
    __tablename__ = "agent_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE")
    )
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(16))
    languages_reviewed: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    review: Mapped[ReviewRow] = relationship(back_populates="agent_results")

    findings: Mapped[list[FindingRow]] = relationship(
        back_populates="agent_result", cascade="all, delete-orphan", lazy="selectin"
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_results.id", ondelete="CASCADE")
    )
    severity: Mapped[str] = mapped_column(String(16))
    certainty: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(32))
    file: Mapped[str] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text, default="")
    cwe: Mapped[str | None] = mapped_column(String(32), nullable=True)

    agent_result: Mapped[AgentResultRow] = relationship(back_populates="findings")


class PromptOverrideRow(Base):
    """Runtime override for an agent's base prompt."""

    __tablename__ = "prompt_overrides"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class GlobalConfigRow(Base):
    """Key-value settings configured via the dashboard (e.g. LLM provider)."""

    __tablename__ = "global_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Scan tables (recent changes + maintenance scans)
# ---------------------------------------------------------------------------


class ScanRow(Base):
    """A completed (or in-progress) scan."""

    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scan_type: Mapped[str] = mapped_column(String(32), index=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    platform: Mapped[str] = mapped_column(String(16))

    time_window_days: Mapped[int] = mapped_column(Integer, default=7)
    staleness_months: Mapped[int] = mapped_column(Integer, default=6)

    total_findings: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")

    stage: Mapped[str] = mapped_column(String(32), default="queued")
    stage_detail: Mapped[str] = mapped_column(Text, default="")
    pipeline_log: Mapped[list] = mapped_column(JSONB, default=list)

    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    agent_results: Mapped[list[ScanAgentResultRow]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", lazy="selectin"
    )


class ScanAgentResultRow(Base):
    __tablename__ = "scan_agent_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE")
    )
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan: Mapped[ScanRow] = relationship(back_populates="agent_results")
    findings: Mapped[list[ScanFindingRow]] = relationship(
        back_populates="agent_result", cascade="all, delete-orphan", lazy="selectin"
    )


class ScanFindingRow(Base):
    __tablename__ = "scan_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scan_agent_results.id", ondelete="CASCADE")
    )
    severity: Mapped[str] = mapped_column(String(16))
    certainty: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(64))
    file: Mapped[str] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effort_estimate: Mapped[str | None] = mapped_column(String(16), nullable=True)

    agent_result: Mapped[ScanAgentResultRow] = relationship(back_populates="findings")
