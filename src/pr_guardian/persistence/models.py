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

    comment_mode: Mapped[str] = mapped_column(
        String(32), server_default="none", nullable=False
    )

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
    verdict_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

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


# ---------------------------------------------------------------------------
# Scan issue tracking
# ---------------------------------------------------------------------------


class ScanIssueRow(Base):
    """Tracks platform issues created from scan findings."""

    __tablename__ = "scan_issues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    # JSON list of ScanFindingRow UUIDs covered by this issue
    finding_ids: Mapped[list] = mapped_column(JSONB, default=list)
    issue_url: Mapped[str] = mapped_column(Text, default="")
    issue_number: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[str] = mapped_column(String(16), default="")
    repo: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Finding dismissals (feedback loop)
# ---------------------------------------------------------------------------


class FindingDismissalRow(Base):
    """Author dismissal/comment on a finding, scoped per-PR across reviews."""

    __tablename__ = "finding_dismissals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pr_id: Mapped[str] = mapped_column(String(64), index=True)
    repo: Mapped[str] = mapped_column(String(256))
    platform: Mapped[str] = mapped_column(String(16))
    signature: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24))  # by_design | false_positive | acknowledged | will_fix
    comment: Mapped[str] = mapped_column(Text, default="")
    source_finding: Mapped[dict] = mapped_column(JSONB, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Inline comment tracking
# ---------------------------------------------------------------------------


class PostedInlineCommentRow(Base):
    """Tracks platform-native comment IDs posted per review."""

    __tablename__ = "posted_inline_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id"), index=True
    )
    platform_comment_id: Mapped[str] = mapped_column(String(256))
    platform: Mapped[str] = mapped_column(String(16))
    pr_id: Mapped[str] = mapped_column(String(64))
    repo: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# GitHub PAT management
# ---------------------------------------------------------------------------


class GithubPatRow(Base):
    """A named GitHub Personal Access Token, stored encrypted."""

    __tablename__ = "github_pats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    encrypted_token: Mapped[str] = mapped_column(Text)
    token_prefix: Mapped[str] = mapped_column(String(20), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Admin & API key management
# ---------------------------------------------------------------------------


class AdminRow(Base):
    """An admin user, identified by email."""

    __tablename__ = "admins"

    email: Mapped[str] = mapped_column(String(256), primary_key=True)
    added_by: Mapped[str] = mapped_column(String(256), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ApiKeyRow(Base):
    """An API key for machine-to-machine authentication."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(12))  # "prg_xxxx" for display
    scopes: Mapped[list] = mapped_column(JSONB, default=lambda: ["read"])
    created_by: Mapped[str] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# PR Dashboard: user identity + sync sources + cached open PRs
# ---------------------------------------------------------------------------


class UserIdentityRow(Base):
    """Per-user mapping of email → GitHub handle + ADO UPN."""

    __tablename__ = "user_identities"

    email: Mapped[str] = mapped_column(String(256), primary_key=True)
    github_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ado_upn: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SyncSourceRow(Base):
    """A repo being actively tracked by the PR sync worker."""

    __tablename__ = "sync_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(16), index=True)  # github | ado
    org: Mapped[str] = mapped_column(String(256))
    project: Mapped[str] = mapped_column(String(256), default="")  # ADO only
    repo: Mapped[str] = mapped_column(String(256))  # "owner/name" for GH, "name" for ADO
    repo_url: Mapped[str] = mapped_column(Text, default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SyncedPRRow(Base):
    """Cached open PR from GitHub or ADO."""

    __tablename__ = "synced_prs"
    __table_args__ = (
        # Used for upserts — uniquely identifies a PR across platforms
        __import__("sqlalchemy").UniqueConstraint(
            "platform", "pr_id", "repo", "project", name="uq_synced_pr"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(16), index=True)
    pr_id: Mapped[str] = mapped_column(String(64))
    org: Mapped[str] = mapped_column(String(256), index=True)
    project: Mapped[str] = mapped_column(String(256), default="")  # ADO only
    repo: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(256), index=True)  # login or UPN
    author_display: Mapped[str] = mapped_column(String(256), default="")
    pr_url: Mapped[str] = mapped_column(Text, default="")
    source_branch: Mapped[str] = mapped_column(String(256), default="")
    target_branch: Mapped[str] = mapped_column(String(256), default="")
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    has_conflicts: Mapped[bool] = mapped_column(Boolean, default=False)
    # approved | changes_requested | pending
    approval_status: Mapped[str] = mapped_column(String(32), default="pending")
    reviewers: Mapped[list] = mapped_column(JSONB, default=list)  # list of usernames
    assignees: Mapped[list] = mapped_column(JSONB, default=list)  # list of usernames
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    # 'success' | 'failure' | 'pending' | 'unknown'
    ci_status: Mapped[str] = mapped_column(String(32), default="unknown")
    pr_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pr_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ExcludedRepoRow(Base):
    """Admin-side repo exclusion: repos hidden from the PR dashboard."""

    __tablename__ = "excluded_repos"
    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint(
            "platform", "org", "project", "repo", name="uq_excluded_repo"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(16))
    org: Mapped[str] = mapped_column(String(256))
    project: Mapped[str] = mapped_column(String(256), default="")
    repo: Mapped[str] = mapped_column(String(256))
    excluded_by_email: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
