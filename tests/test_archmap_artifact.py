import json
from pathlib import Path

from pr_guardian.config.schema import GuardianConfig
from pr_guardian.decision.engine import decide
from pr_guardian.discovery.archmap import parse_archmap_artifact
from pr_guardian.models.context import (
    ArchmapContext,
    ArchmapFile,
    BlastRadius,
    ChangeProfile,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    SecuritySurface,
)
from pr_guardian.models.findings import AgentResult, Verdict
from pr_guardian.models.languages import LanguageMap
from pr_guardian.models.output import Decision
from pr_guardian.models.pr import Diff, Platform, PlatformPR


def _artifact(**overrides) -> str:
    payload = {
        "version": 1,
        "commit": "abc123",
        "generatedAt": "2026-05-30T10:00:00.000Z",
        "scope": {
            "requested": ["src/core/router.ts", "src/deleted.ts"],
            "missing": ["src/deleted.ts"],
        },
        "files": {
            "src/core/router.ts": {
                "class": "hub",
                "ca": 14,
                "tca": 63,
                "instability": 0.06,
                "risk": 100,
                "overridden": False,
                "reason": "Ca=14 (14 direct, 63 transitive)",
                "dependents": ["src/api/login.ts", "src/ui/App.tsx"],
            },
            "src/unrelated.ts": {
                "class": "hub",
                "ca": 20,
                "tca": 90,
                "instability": 0.02,
                "risk": 99,
                "overridden": False,
                "reason": "not in this PR",
                "dependents": [],
            },
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


def _context_with_archmap(archmap: ArchmapContext) -> ReviewContext:
    return ReviewContext(
        pr=PlatformPR(
            platform=Platform.GITHUB,
            pr_id="42",
            repo="org/repo",
            repo_url="",
            source_branch="feature",
            target_branch="develop",
            author="dev",
            title="Change shared router",
            head_commit_sha="abc123",
        ),
        repo_path=Path("/tmp"),
        diff=Diff(),
        changed_files=["src/core/router.ts"],
        lines_changed=12,
        language_map=LanguageMap(),
        primary_language="typescript",
        cross_stack=False,
        repo_risk_class=RepoRiskClass.STANDARD,
        security_surface=SecuritySurface(),
        blast_radius=BlastRadius(),
        archmap=archmap,
        change_profile=ChangeProfile(),
    )


def test_parse_archmap_artifact_filters_to_changed_files():
    ctx = parse_archmap_artifact(
        _artifact(),
        expected_commit="abc123",
        changed_files=["./src/core/router.ts"],
    )

    assert ctx.error == ""
    assert list(ctx.files) == ["src/core/router.ts"]
    assert ctx.scope_missing == ["src/deleted.ts"]
    hub = ctx.files["src/core/router.ts"]
    assert hub.classification == "hub"
    assert hub.risk == 100
    assert hub.dependents == ("src/api/login.ts", "src/ui/App.tsx")


def test_parse_archmap_artifact_rejects_stale_commit():
    ctx = parse_archmap_artifact(
        _artifact(commit="oldsha"),
        expected_commit="abc123",
        changed_files=["src/core/router.ts"],
    )

    assert "does not match PR head" in ctx.error
    assert ctx.files == {}


def test_archmap_hub_forces_human_review():
    archmap = ArchmapContext(
        commit="abc123",
        files={
            "src/core/router.ts": ArchmapFile(
                path="src/core/router.ts",
                classification="hub",
                ca=14,
                tca=63,
                instability=0.06,
                risk=100,
                overridden=False,
                reason="Ca=14",
                dependents=("src/api/login.ts",),
            )
        },
    )
    agent = AgentResult(agent_name="code_quality_observability", verdict=Verdict.PASS)

    result = decide(_context_with_archmap(archmap), [agent], RiskTier.LOW, GuardianConfig())

    assert result.decision == Decision.HUMAN_REVIEW
    assert any(trigger.kind == "archmap_hub" for trigger in result.sticky_triggers)
