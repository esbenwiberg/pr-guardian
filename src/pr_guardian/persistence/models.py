"""SQLAlchemy ORM models for review persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _json_type():
    return JSON().with_variant(JSONB, "postgresql")


class ReviewRow(Base):
    """A completed (or in-progress) PR review."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    trust_tier_details: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    combined_score: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(String(32), default="pending")

    mechanical_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    override_reasons: Mapped[dict | list] = mapped_column(_json_type(), default=list)
    summary: Mapped[str] = mapped_column(Text, default="")

    # Pipeline stage tracking for live progress
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    stage_detail: Mapped[str] = mapped_column(Text, default="")
    pipeline_log: Mapped[list] = mapped_column(_json_type(), default=list)

    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    comment_mode: Mapped[str] = mapped_column(String(32), server_default="none", nullable=False)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True
    )
    profile_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=True
    )
    connection_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    repo_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repo_links.id"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("readiness_candidates.id"), nullable=True
    )
    review_source: Mapped[str] = mapped_column(String(32), default="manual")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    mechanical_results: Mapped[list[MechanicalResultRow]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )
    agent_results: Mapped[list[AgentResultRow]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )

    postback_meta: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)


class MechanicalResultRow(Base):
    __tablename__ = "mechanical_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE")
    )
    tool: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    findings: Mapped[list] = mapped_column(_json_type(), default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    review: Mapped[ReviewRow] = relationship(back_populates="mechanical_results")


class AgentResultRow(Base):
    __tablename__ = "agent_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE")
    )
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(16))
    languages_reviewed: Mapped[list] = mapped_column(_json_type(), default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    review: Mapped[ReviewRow] = relationship(back_populates="agent_results")

    findings: Mapped[list[FindingRow]] = relationship(
        back_populates="agent_result", cascade="all, delete-orphan", lazy="selectin"
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_type: Mapped[str] = mapped_column(String(32), index=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    platform: Mapped[str] = mapped_column(String(16))

    time_window_days: Mapped[int] = mapped_column(Integer, default=7)
    staleness_months: Mapped[int] = mapped_column(Integer, default=6)

    total_findings: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")

    stage: Mapped[str] = mapped_column(String(32), default="queued")
    stage_detail: Mapped[str] = mapped_column(Text, default="")
    pipeline_log: Mapped[list] = mapped_column(_json_type(), default=list)

    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True
    )
    profile_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=True
    )
    connection_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    repo_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repo_links.id"), nullable=True
    )
    scan_source: Mapped[str] = mapped_column(String(32), default="scan")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    agent_results: Mapped[list[ScanAgentResultRow]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", lazy="selectin"
    )


class ScanAgentResultRow(Base):
    __tablename__ = "scan_agent_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    # JSON list of ScanFindingRow UUIDs covered by this issue
    finding_ids: Mapped[list] = mapped_column(_json_type(), default=list)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pr_id: Mapped[str] = mapped_column(String(64), index=True)
    repo: Mapped[str] = mapped_column(String(256))
    platform: Mapped[str] = mapped_column(String(16))
    signature: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(
        String(24)
    )  # by_design | false_positive | acknowledged | will_fix
    comment: Mapped[str] = mapped_column(Text, default="")
    source_finding: Mapped[dict] = mapped_column(_json_type(), default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    resolution_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    fixed_by_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    regressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    regressed_from_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ---------------------------------------------------------------------------
# Inline comment tracking
# ---------------------------------------------------------------------------


class PostedInlineCommentRow(Base):
    """Tracks platform-native comment IDs posted per review."""

    __tablename__ = "posted_inline_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id"), index=True
    )
    platform_comment_id: Mapped[str] = mapped_column(String(256), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    pr_id: Mapped[str] = mapped_column(String(64))
    repo: Mapped[str] = mapped_column(String(256))
    # Finding payloads carried by this comment, so a reply-to-comment dismissal
    # can be mapped back to the specific finding(s). See inline_finding_payload.
    findings: Mapped[list] = mapped_column(_json_type(), default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Guidance comment tracking (one sticky comment per PR per platform)
# ---------------------------------------------------------------------------


class GuidanceCommentRow(Base):
    """Tracks the sticky guidance comment ID posted on a PR."""

    __tablename__ = "guidance_comments"
    __table_args__ = (
        UniqueConstraint("platform", "repo", "pr_id", name="uq_guidance_comment_pr"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    pr_id: Mapped[str] = mapped_column(String(64), index=True)
    comment_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# ChatOps command tracking
# ---------------------------------------------------------------------------


class ChatOpsCommandRow(Base):
    """Idempotency/audit row for platform comments that trigger Guardian work."""

    __tablename__ = "chatops_commands"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_id",
            "command",
            name="uq_chatops_command_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    repo: Mapped[str] = mapped_column(String(256), index=True)
    pr_id: Mapped[str] = mapped_column(String(64), index=True)
    command: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(64), default="")
    actor: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="claimed")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(_json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Profiles, connections, repo links, and readiness candidates
# ---------------------------------------------------------------------------


class ProfileRow(Base):
    """Guardian-owned reusable review and scan policy."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict] = mapped_column(_json_type(), default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(256), default="system")
    updated_by: Mapped[str] = mapped_column(String(256), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ConnectionRow(Base):
    """Named outbound platform credential. Secret values are encrypted."""

    __tablename__ = "connections"
    __table_args__ = (
        Index(
            "uq_connections_single_default_github",
            "is_default",
            unique=True,
            postgresql_where=__import__("sqlalchemy").text(
                "platform = 'github' AND is_default = TRUE AND archived_at IS NULL"
            ),
            sqlite_where=__import__("sqlalchemy").text(
                "platform = 'github' AND is_default = 1 AND archived_at IS NULL"
            ),
        ),
        CheckConstraint("platform in ('github', 'ado')", name="ck_connections_platform"),
        CheckConstraint(
            "health_status in ('unknown', 'healthy', 'unhealthy')",
            name="ck_connections_health_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    platform: Mapped[str] = mapped_column(String(16), index=True)
    # auth_kind is None for legacy PAT rows; "github_app" for GitHub App Connections
    auth_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    org_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Legacy PAT / ADO token storage
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_prefix: Mapped[str] = mapped_column(String(20), default="")
    # GitHub App credential fields
    app_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    installation_account: Mapped[str | None] = mapped_column(String(256), nullable=True)
    installation_target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    encrypted_private_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    private_key_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    app_permissions: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")
    health_message: Mapped[str] = mapped_column(Text, default="")
    health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(256), default="system")
    updated_by: Mapped[str] = mapped_column(String(256), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RepoLinkRow(Base):
    """Exact repository opt-in tying one repository to one Profile and Connection."""

    __tablename__ = "repo_links"
    __table_args__ = (
        Index(
            "uq_repo_links_active_canonical",
            "platform",
            "canonical_repo_key",
            unique=True,
            postgresql_where=__import__("sqlalchemy").text("archived_at IS NULL"),
            sqlite_where=__import__("sqlalchemy").text("archived_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    org_url: Mapped[str] = mapped_column(Text, default="")
    project: Mapped[str] = mapped_column(String(256), default="")
    repo_owner: Mapped[str] = mapped_column(String(256), default="")
    repo_name: Mapped[str] = mapped_column(String(256))
    repo_url: Mapped[str] = mapped_column(Text, default="")
    canonical_repo_key: Mapped[str] = mapped_column(String(512), index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id")
    )
    auto_review_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    require_review_check: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(256), default="system")
    updated_by: Mapped[str] = mapped_column(String(256), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ProfileManagerRow(Base):
    """Signed-in user allowed to manage Profiles and Connections."""

    __tablename__ = "profile_managers"

    email: Mapped[str] = mapped_column(String(256), primary_key=True)
    added_by: Mapped[str] = mapped_column(String(256), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ProfileAuditEventRow(Base):
    """Append-only audit history for profile/connection/link management."""

    __tablename__ = "profile_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(256), default="system")
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    before: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    after: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ReadinessCandidateRow(Base):
    """Durable readiness state for one PR head SHA on an opted-in repo."""

    __tablename__ = "readiness_candidates"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "canonical_repo_key",
            "pr_id",
            "head_sha",
            name="uq_readiness_candidate_sha",
        ),
        CheckConstraint(
            "state in ('waiting', 'blocked', 'reviewing', 'reviewed', 'superseded', 'error')",
            name="ck_readiness_candidates_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repo_links.id"), index=True
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(16), index=True)
    org_url: Mapped[str] = mapped_column(Text, default="")
    project: Mapped[str] = mapped_column(String(256), default="")
    repo_owner: Mapped[str] = mapped_column(String(256), default="")
    repo_name: Mapped[str] = mapped_column(String(256))
    repo: Mapped[str] = mapped_column(String(512), index=True)
    canonical_repo_key: Mapped[str] = mapped_column(String(512), index=True)
    pr_id: Mapped[str] = mapped_column(String(64), index=True)
    pr_url: Mapped[str] = mapped_column(Text, default="")
    head_sha: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(16), default="waiting")
    reason: Mapped[str] = mapped_column(String(128), default="")
    readiness_snapshot: Mapped[dict] = mapped_column(_json_type(), default=dict)
    profile_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    connection_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    transitions: Mapped[list["ReadinessCandidateTransitionRow"]] = relationship(
        back_populates="candidate",
        lazy="selectin",
        order_by="ReadinessCandidateTransitionRow.created_at",
    )


class ReadinessCandidateTransitionRow(Base):
    """Append-only readiness candidate transition history."""

    __tablename__ = "readiness_candidate_transitions"
    __table_args__ = (
        CheckConstraint(
            "from_state is null or from_state in "
            "('waiting', 'blocked', 'reviewing', 'reviewed', 'superseded', 'error')",
            name="ck_readiness_transitions_from_state",
        ),
        CheckConstraint(
            "to_state in ('waiting', 'blocked', 'reviewing', 'reviewed', 'superseded', 'error')",
            name="ck_readiness_transitions_to_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("readiness_candidates.id"), index=True
    )
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_state: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64), default="")
    actor: Mapped[str] = mapped_column(String(256), default="")
    reason: Mapped[str] = mapped_column(String(128), default="")
    readiness_snapshot: Mapped[dict] = mapped_column(_json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    candidate: Mapped[ReadinessCandidateRow] = relationship(back_populates="transitions")


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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(12))  # "prg_xxxx" for display
    scopes: Mapped[list] = mapped_column(_json_type(), default=lambda: ["read"])
    created_by: Mapped[str] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16), index=True)  # github | ado
    org: Mapped[str] = mapped_column(String(256))
    project: Mapped[str] = mapped_column(String(256), default="")  # ADO only
    repo: Mapped[str] = mapped_column(String(256))  # "owner/name" for GH, "name" for ADO
    repo_url: Mapped[str] = mapped_column(Text, default="")
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=True
    )
    connection_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    # approved | changes_requested | pending | draft | merged
    approval_status: Mapped[str] = mapped_column(String(32), default="pending")
    reviewers: Mapped[list] = mapped_column(_json_type(), default=list)  # list of usernames
    assignees: Mapped[list] = mapped_column(_json_type(), default=list)  # list of usernames
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    # 'success' | 'failure' | 'pending' | 'unknown'
    ci_status: Mapped[str] = mapped_column(String(32), default="unknown")
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True
    )
    profile_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=True
    )
    connection_snapshot: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    repo_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repo_links.id"), nullable=True
    )
    sync_source: Mapped[str] = mapped_column(String(32), default="sync")
    pr_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pr_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))
    org: Mapped[str] = mapped_column(String(256))
    project: Mapped[str] = mapped_column(String(256), default="")
    repo: Mapped[str] = mapped_column(String(256))
    excluded_by_email: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ExclusionRuleRow(Base):
    """Admin-defined wildcard exclusion: repos matching any rule are hidden everywhere.

    Each pattern field uses fnmatch syntax (`*`, `?`, char classes). An empty pattern
    means "match any value for this field" — so a rule with org_pattern="acme" and
    repo_pattern="" excludes every repo under the acme org.
    """

    __tablename__ = "exclusion_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))
    org_pattern: Mapped[str] = mapped_column(String(256), default="")
    project_pattern: Mapped[str] = mapped_column(String(256), default="")
    repo_pattern: Mapped[str] = mapped_column(String(256), default="")
    created_by_email: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
