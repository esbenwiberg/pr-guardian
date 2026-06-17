from __future__ import annotations

import asyncio
import inspect
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog

from pr_guardian.agents.architecture_intent import ArchitectureIntentAgent
from pr_guardian.agents.code_quality_obs import CodeQualityObservabilityAgent
from pr_guardian.agents.hotspot import HotspotAgent
from pr_guardian.agents.performance import PerformanceAgent
from pr_guardian.agents.security_privacy import SecurityPrivacyAgent
from pr_guardian.agents.test_quality import TestQualityAgent
from pr_guardian.config.loader import apply_global_settings
from pr_guardian.config.profile_resolver import (
    resolve_default_profile_config,
    resolve_profile_snapshot_config,
)
from pr_guardian.config.schema import GuardianConfig
from pr_guardian.core.events import ReviewEvent, event_bus
from pr_guardian.decision.actions import (
    SEVERITY_ORDER,
    build_review_detail_url,
    build_summary_comment,
    get_review_labels,
)
from pr_guardian.decision.engine import decide
from pr_guardian.decision.severity_filter import filter_findings
from pr_guardian.decision.validator import validate_findings
from pr_guardian.discovery.blast_radius import compute_blast_radius
from pr_guardian.discovery.archmap import parse_archmap_artifact
from pr_guardian.discovery.change_profile import build_change_profile
from pr_guardian.discovery.dep_graph import build_dep_graph
from pr_guardian.languages.detector import detect_languages
from pr_guardian.mechanical.runner import all_checks_passed, run_mechanical_checks
from pr_guardian.decision.types import StickyTrigger
from pr_guardian.models.context import (
    ArchmapContext,
    RepoRiskClass,
    ReviewContext,
    RiskTier,
    TrustTier,
)
from pr_guardian.models.findings import AgentResult, Certainty, Finding, Severity, Verdict
from pr_guardian.models.output import Decision, MechanicalResult, ReviewResult
from pr_guardian.models.pr import PlatformPR
from pr_guardian.platform.guidance import upsert_guidance_comment as _upsert_guidance_comment
from pr_guardian.platform.protocol import PlatformAdapter
from pr_guardian.triage.classifier import classify
from pr_guardian.triage.hotspots import load_hotspots
from pr_guardian.triage.surface_map import build_security_surface
from pr_guardian.triage.trust_classifier import classify_trust_tier
from pr_guardian.triage.trust_escalation import maybe_escalate_trust

log = structlog.get_logger()

# Per-million-token pricing (input, output) per million tokens.
# More-specific prefixes must come before shorter substrings (gpt-5.5 before gpt-5).
_TOKEN_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    # OpenAI GPT-5 family — longest prefix first
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4": (2.50, 15.0),
    "gpt-5.2": (0.875, 7.0),
    "gpt-5": (0.625, 5.0),
}
_DEFAULT_PRICE = (3.0, 15.0)  # fallback


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts and model name (best-effort match)."""
    model_lower = model.lower()
    price = _DEFAULT_PRICE
    for prefix, p in _TOKEN_PRICES.items():
        if prefix in model_lower:
            price = p
            break
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def _try_import_storage():
    """Lazily import storage to avoid failures when DB is not configured."""
    try:
        from pr_guardian.persistence import storage

        return storage
    except Exception:
        return None


def get_storage():
    """Public accessor for lazily-imported storage (None when DB is not configured)."""
    return _try_import_storage()


AGENT_REGISTRY = {
    "security_privacy": SecurityPrivacyAgent,
    "performance": PerformanceAgent,
    "architecture_intent": ArchitectureIntentAgent,
    "code_quality_observability": CodeQualityObservabilityAgent,
    "test_quality": TestQualityAgent,
    "hotspot": HotspotAgent,
}


async def _load_archmap_context(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    changed_files: list[str],
    _plog,
) -> ArchmapContext:
    """Best-effort load of Archmap's optional PR artifact."""
    try:
        raw_artifact = await adapter.fetch_archmap_artifact(pr)
    except Exception as exc:
        log.warning("archmap_artifact_fetch_failed", pr_id=pr.pr_id, repo=pr.repo, error=str(exc))
        _plog("warn", "discovery", f"Archmap artifact unavailable: {exc}")
        return ArchmapContext(error=str(exc))

    if not raw_artifact:
        _plog("info", "discovery", "No Archmap artifact found for this PR head SHA.")
        return ArchmapContext()

    archmap = parse_archmap_artifact(
        raw_artifact,
        expected_commit=pr.head_commit_sha,
        changed_files=changed_files,
    )
    if archmap.error:
        _plog("warn", "discovery", f"Archmap artifact ignored: {archmap.error}")
        return archmap

    hubs = archmap.hub_files()
    _plog(
        "info",
        "discovery",
        f"Archmap artifact loaded: {len(archmap.files)} changed file(s), {len(hubs)} hub(s).",
    )
    if archmap.scope_missing:
        _plog(
            "warn",
            "discovery",
            "Archmap scope missing file(s): " + ", ".join(archmap.scope_missing[:10]),
        )
    return archmap


async def run_review(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    service_config: GuardianConfig | None = None,
    *,
    existing_review_db_id: uuid.UUID | None = None,
    post_comment: bool = True,
    base_url: str = "",
    dismissals: list[dict] | None = None,
    diff_override=None,
    skip_platform_side_effects: bool = False,
    comment_mode: str = "summary",
    pat_name: str | None = None,
    manual_comment_override: bool = False,
) -> ReviewResult:
    """Main review pipeline: Discovery → Mechanical → Triage → Agents → Decision."""
    log.info("review_started", pr_id=pr.pr_id, repo=pr.repo)

    storage = _try_import_storage()
    review_db_id: uuid.UUID | None = existing_review_db_id

    # Create DB record only if one wasn't provided by the caller
    if storage and review_db_id is None:
        try:
            review_db_id = await storage.create_review_record(
                pr, comment_mode=comment_mode, pat_name=pat_name
            )
        except Exception as e:
            log.warning("db_create_failed", error=str(e))

    pipeline_log: list[dict] = []

    def _plog(level: str, stage: str, msg: str, **extra):
        pipeline_log.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "stage": stage,
                "msg": msg,
                **{k: v for k, v in extra.items() if v is not None},
            }
        )

    def _emit(stage: str, detail: str = "", **extra):
        event_bus.publish(
            ReviewEvent(
                review_id=str(review_db_id) if review_db_id else "",
                pr_id=pr.pr_id,
                repo=pr.repo,
                stage=stage,
                detail=detail,
                extra=extra,
            )
        )

    async def _update_stage(stage: str, detail: str = ""):
        _emit(stage, detail)
        if storage and review_db_id:
            try:
                await storage.update_review_stage(review_db_id, stage, detail)
            except Exception as e:
                log.warning("db_stage_update_failed", stage=stage, error=str(e))

    side_effects_skipped = skip_platform_side_effects or await _is_stale_automatic_review(
        adapter, pr, storage=storage, review_id=review_db_id
    )

    # Set pending review status (skip for synthetic PRs like repo reviews or stale automatic runs)
    if not side_effects_skipped:
        review_url = build_review_detail_url(str(review_db_id or ""), base_url) or ""
        try:
            await _post_review_status(
                adapter,
                pr,
                "pending",
                "Guardian review in progress",
                target_url=review_url,
            )
        except Exception as e:
            log.warning("set_status_failed", error=str(e))
        # Update guidance comment with review-in-progress state and deeplink
        await _upsert_guidance_comment(
            adapter, pr, "reviewing", review_url=review_url, storage=storage
        )

    # Link the previous completed review for this PR so re-reviews clean up their
    # predecessor's inline comments instead of orphaning them. Resolved centrally
    # here so every entry point — webhook autoreview, poll fallback, manual start —
    # inherits the cleanup. The current review is still in-flight (finished_at is
    # NULL), so find_review_by_pr_url returns the prior one, not this one.
    original_review_id: str | None = None
    if storage and not side_effects_skipped:
        try:
            prior = await storage.find_review_by_pr_url(pr.pr_url)
            if prior and str(prior.get("id")) != str(review_db_id):
                original_review_id = str(prior["id"])
        except Exception as e:
            log.warning("prior_review_lookup_failed", pr_id=pr.pr_id, error=str(e))

    try:
        return await _run_pipeline(
            pr,
            adapter,
            service_config,
            storage,
            review_db_id,
            pipeline_log,
            _plog,
            _emit,
            _update_stage,
            post_comment=post_comment and not skip_platform_side_effects,
            base_url=base_url,
            dismissals=dismissals,
            diff_override=diff_override,
            skip_platform_side_effects=side_effects_skipped,
            comment_mode=comment_mode,
            manual_comment_override=manual_comment_override,
            original_review_id=original_review_id,
        )
    except Exception as exc:
        if storage and review_db_id:
            try:
                await storage.mark_review_failed(review_db_id, str(exc), pipeline_log=pipeline_log)
            except Exception as db_err:
                log.warning("db_mark_failed_error", error=str(db_err))
        _emit("error", str(exc))
        raise


async def _run_pipeline(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    service_config: GuardianConfig | None,
    storage,
    review_db_id: uuid.UUID | None,
    pipeline_log: list[dict],
    _plog,
    _emit,
    _update_stage,
    *,
    post_comment: bool = True,
    base_url: str = "",
    dismissals: list[dict] | None = None,
    diff_override=None,
    skip_platform_side_effects: bool = False,
    comment_mode: str = "summary",
    manual_comment_override: bool = False,
    original_review_id: str | None = None,
) -> ReviewResult:
    """Inner pipeline logic, separated so run_review can handle errors."""

    # Fetch diff (or use pre-built synthetic diff, e.g. for repo reviews)
    if diff_override is not None:
        diff = diff_override
        _plog(
            "info",
            "discovery",
            f"Using pre-built diff ({len(diff.files)} files) — skipping fetch_diff.",
        )
    else:
        diff = await adapter.fetch_diff(pr)
    changed_files = diff.file_paths
    files_with_patch = sum(1 for f in diff.files if f.patch)
    _plog(
        "info",
        "discovery",
        f"Fetched diff: {len(changed_files)} files, {files_with_patch} with patch content.",
    )
    if changed_files and files_with_patch == 0:
        _plog(
            "warn", "discovery", "No patch content retrieved — agents will have no code to review."
        )

    # Use temp dir as repo_path (in production, would be a shallow clone)
    repo_path = Path(tempfile.mkdtemp(prefix=f"review-{pr.pr_id}-"))

    # Stage 0: Discovery
    await _update_stage("discovery", "Parsing diff and building context")
    if service_config is None:
        config = (await resolve_default_profile_config()).config
    else:
        config = await apply_global_settings(service_config)

    language_map = detect_languages(changed_files)
    security_surface = build_security_surface(config.security_surface, changed_files)
    dep_graph = build_dep_graph(config.path_risk.critical_consumers or None)
    blast_radius = compute_blast_radius(changed_files, security_surface, dep_graph)
    change_profile = build_change_profile(
        changed_files,
        diff,
        security_surface,
        blast_radius,
        config.file_roles,
    )
    hotspots = await load_hotspots(pr.repo)
    archmap = await _load_archmap_context(pr, adapter, changed_files, _plog)

    risk_class_map = {
        "standard": RepoRiskClass.STANDARD,
        "elevated": RepoRiskClass.ELEVATED,
        "critical": RepoRiskClass.CRITICAL,
    }

    context = ReviewContext(
        pr=pr,
        repo_path=repo_path,
        diff=diff,
        changed_files=changed_files,
        lines_changed=diff.lines_changed,
        language_map=language_map,
        primary_language=language_map.primary_language,
        cross_stack=language_map.cross_stack,
        repo_config=config.model_dump(),
        repo_risk_class=risk_class_map.get(config.repo_risk_class, RepoRiskClass.STANDARD),
        hotspots=hotspots,
        security_surface=security_surface,
        blast_radius=blast_radius,
        archmap=archmap,
        change_profile=change_profile,
    )

    # Trust tier classification (path-based, deterministic)
    trust_tier_result = classify_trust_tier(
        changed_files,
        config,
        context.repo_risk_class,
    )
    context.trust_tier_result = trust_tier_result
    _plog(
        "info",
        "discovery",
        f"Trust tier: {trust_tier_result.resolved_tier.value}. "
        f"Triggering files: {', '.join(trust_tier_result.triggering_files[:5]) or 'none'}.",
    )

    langs = list(language_map.languages.keys())
    _plog(
        "info",
        "discovery",
        f"Parsed {len(changed_files)} files across {len(langs)} language(s): {', '.join(langs)}. "
        f"{diff.lines_changed} lines changed.",
    )
    if security_surface.has_hits():
        surface_files = list(security_surface.classifications.keys())
        _plog(
            "info",
            "discovery",
            f"Security surface files: {', '.join(surface_files[:10])}"
            f"{f' (+{len(surface_files) - 10} more)' if len(surface_files) > 10 else ''}",
        )
    log.info(
        "discovery_complete",
        languages=langs,
        files=len(changed_files),
        lines=diff.lines_changed,
    )

    # Stage 1: Mechanical Gates
    await _update_stage("mechanical", "Running mechanical checks")
    mechanical_results = await run_mechanical_checks(
        repo_path,
        language_map,
        changed_files,
        config,
        pr.target_branch,
    )

    passed_count = sum(1 for r in mechanical_results if r.passed)
    total_count = len(mechanical_results)
    _plog("info", "mechanical", f"Mechanical checks: {passed_count}/{total_count} passed.")
    for r in mechanical_results:
        if not r.passed:
            _plog(
                "warn",
                "mechanical",
                f"{r.tool}: FAILED — {r.error or f'{len(r.findings)} finding(s)'}",
            )

    if not all_checks_passed(mechanical_results):
        log.info("mechanical_gate_failed", pr_id=pr.pr_id)
        from pr_guardian.models.context import RiskTier

        _plog("error", "mechanical", "Mechanical gate failed — PR blocked.")
        result = ReviewResult(
            pr_id=pr.pr_id,
            repo=pr.repo,
            risk_tier=RiskTier.HIGH,
            repo_risk_class=context.repo_risk_class,
            review_id=str(review_db_id) if review_db_id else "",
            mechanical_results=[_convert_mechanical(r) for r in mechanical_results],
            mechanical_passed=False,
            decision=Decision.HARD_BLOCK,
            summary="Mechanical checks failed — PR blocked.",
            pipeline_log=pipeline_log,
        )
        side_effects_skipped = skip_platform_side_effects or await _is_stale_automatic_review(
            adapter, pr, storage=storage, review_id=review_db_id
        )
        if post_comment and not side_effects_skipped:
            await _post_results(
                adapter,
                pr,
                result,
                config,
                base_url=base_url,
                comment_mode=comment_mode,
                review_id=review_db_id,
                storage=storage,
                original_review_id=original_review_id,
                manual_comment_override=manual_comment_override,
            )
        await _save_result(storage, review_db_id, result, _emit)
        return result

    # Stage 2: Triage
    await _update_stage("triage", "Classifying risk and selecting agents")
    triage_result = classify(context, config)
    _plog(
        "info",
        "triage",
        f"Risk tier: {triage_result.risk_tier.value}. "
        f"Agents selected: {', '.join(sorted(triage_result.agent_set)) or 'none'}.",
    )
    for reason in triage_result.reasons:
        _plog("info", "triage", f"Reason: {reason}")
    log.info(
        "triage_complete",
        tier=triage_result.risk_tier.value,
        agents=sorted(triage_result.agent_set),
    )

    # Build per-agent dismissal context strings
    agent_dismissal_context: dict[str, str] = {}
    if dismissals:
        for agent_name in triage_result.agent_set:
            relevant = [
                d
                for d in dismissals
                if d.get("source_finding", {}).get("agent_name") == agent_name
            ]
            if relevant:
                lines = [
                    "## Previously Dismissed Findings",
                    "The PR author has reviewed the following findings and provided context.",
                    "Do not re-flag dismissed items unless new code changes make them relevant again.",
                    "",
                ]
                for i, d in enumerate(relevant, 1):
                    sf = d.get("source_finding", {})
                    lines.append(
                        f"{i}. [{sf.get('file', '?')} :: {sf.get('category', '?')}] "
                        f"Status: {d['status']}"
                    )
                    if d.get("comment"):
                        lines.append(f'   Author: "{d["comment"]}"')
                    lines.append("")
                agent_dismissal_context[agent_name] = "\n".join(lines)
        if agent_dismissal_context:
            _plog(
                "info",
                "agents",
                f"Injecting dismissal context for {len(agent_dismissal_context)} agent(s) "
                f"({len(dismissals)} total dismissal(s)).",
            )

    # Stage 3: AI Agents (parallel)
    await _update_stage("agents", f"Running {len(triage_result.agent_set)} AI agents")
    agent_results: list[AgentResult] = []
    if triage_result.agent_set:
        agent_tasks = []
        for agent_name in triage_result.agent_set:
            agent_cls = AGENT_REGISTRY.get(agent_name)
            if agent_cls:
                agent = agent_cls(config)
                agent_tasks.append(
                    agent.review(
                        context, dismissal_context=agent_dismissal_context.get(agent_name)
                    )
                )

        if agent_tasks:
            agent_results = await asyncio.gather(*agent_tasks)

    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    for ar in agent_results:
        extras = ar.extras or {}
        parts = [f"verdict={ar.verdict.value}", f"{len(ar.findings)} finding(s)"]
        if extras.get("model"):
            parts.append(f"model={extras['model']}")
        if extras.get("response_length"):
            parts.append(f"response={extras['response_length']} chars")
        in_tok = extras.get("input_tokens", 0)
        out_tok = extras.get("output_tokens", 0)
        total_input_tokens += in_tok
        total_output_tokens += out_tok
        if in_tok or out_tok:
            agent_cost = _estimate_cost(extras.get("model", ""), in_tok, out_tok)
            total_cost += agent_cost
            parts.append(f"tokens={in_tok}+{out_tok}")
            parts.append(f"cost=${agent_cost:.4f}")
        level = "warn" if ar.verdict.value == "flag_human" else "info"
        _plog(level, "agents", f"Agent {ar.agent_name}: {', '.join(parts)}")
        if ar.error:
            _plog("error", "agents", f"Agent {ar.agent_name} error: {ar.error}")
        if extras.get("raw_response_preview"):
            _plog(
                "debug",
                "agents",
                f"Agent {ar.agent_name} raw response: {extras['raw_response_preview']}",
                agent=ar.agent_name,
            )

    # Trust tier escalation (post-agents, one-way upward)
    trust_tier_result = maybe_escalate_trust(
        context.trust_tier_result,
        agent_results,
        config.trust_tiers,
    )
    context.trust_tier_result = trust_tier_result
    if trust_tier_result.escalated:
        _plog(
            "warn",
            "trust_escalation",
            f"Trust tier escalated: {' | '.join(trust_tier_result.escalation_reasons)}",
        )

    # Stage 4: Decision
    await _update_stage("decision", "Computing final verdict")
    result = decide(context, agent_results, triage_result.risk_tier, config, trust_tier_result)
    result.review_id = str(review_db_id) if review_db_id else ""
    result.mechanical_results = [_convert_mechanical(r) for r in mechanical_results]
    result.mechanical_passed = True

    result.total_input_tokens = total_input_tokens
    result.total_output_tokens = total_output_tokens
    result.cost_usd = round(total_cost, 6)

    _plog(
        "info",
        "decision",
        f"Decision: {result.decision.value}. Score: {result.combined_score:.2f}. "
        f"Risk tier: {result.risk_tier.value}.",
    )
    if total_cost > 0:
        _plog(
            "info",
            "decision",
            f"Total tokens: {total_input_tokens}+{total_output_tokens}. "
            f"Estimated cost: ${total_cost:.4f}.",
        )
    for t in result.sticky_triggers:
        _plog("info", "decision", f"Sticky trigger [{t.kind}]: {t.reason}")
    for reason in result.finding_reasons:
        _plog("info", "decision", f"Finding reason: {reason}")

    # Stage 5: Post-decision noise reduction
    # Severity floor: suppress low-value findings per risk tier (display only,
    # scoring already happened on the full set inside decide()).
    filtered_results, suppressed_count = filter_findings(
        result.agent_results,
        triage_result.risk_tier,
        config,
    )
    result.agent_results = filtered_results
    if suppressed_count:
        _plog(
            "info",
            "noise_reduction",
            f"Severity floor suppressed {suppressed_count} finding(s) "
            f"(risk tier: {triage_result.risk_tier.value}).",
        )

    # Validator: adversarial critic challenges remaining findings.
    remaining_finding_count = sum(len(r.findings) for r in result.agent_results)
    if remaining_finding_count > 0:
        await _update_stage("validation", "Challenging findings with validator agent")
        validated_results, validator_meta = await validate_findings(
            result.agent_results,
            context,
            config,
        )
        result.agent_results = validated_results
        if validator_meta.get("validator_ran"):
            dismissed = validator_meta["dismissed"]
            downgraded = validator_meta["downgraded"]
            val_in = validator_meta.get("input_tokens", 0)
            val_out = validator_meta.get("output_tokens", 0)
            total_input_tokens += val_in
            total_output_tokens += val_out
            if val_in or val_out:
                val_cost = _estimate_cost(
                    validator_meta.get("model") or config.validator.model_override or "",
                    val_in,
                    val_out,
                )
                total_cost += val_cost
            result.total_input_tokens = total_input_tokens
            result.total_output_tokens = total_output_tokens
            result.cost_usd = round(total_cost, 6)
            merged = validator_meta.get("merged", 0)
            clusters_found = validator_meta.get("clusters_found", 0)
            parts = [
                f"Validator: {dismissed} dismissed, {downgraded} downgraded",
            ]
            if merged:
                parts.append(f"{merged} merged ({clusters_found} cluster(s))")
            parts.append(f"out of {remaining_finding_count} finding(s).")
            _plog("info", "noise_reduction", ", ".join(parts))
        if validator_meta.get("error"):
            _plog(
                "warn",
                "noise_reduction",
                f"Validator error (findings kept as-is): {validator_meta['error']}",
            )

    # Post-review dismissal matching
    if dismissals and storage:
        try:
            from pr_guardian.persistence.storage import (
                finding_signature as _fsig,
                match_dismissals_to_findings,
                archive_stale_dismissals,
            )

            # Build list of all findings with agent_name for matching.
            # For merged findings, also register contributing agents' signatures
            # so that dismissals from any contributing agent carry forward.
            all_findings_flat = []
            active_sigs: set[str] = set()
            for ar in result.agent_results:
                for f in ar.findings:
                    agent_name = f.primary_agent or ar.agent_name
                    entry = {
                        "file": f.file,
                        "category": f.category,
                        "agent_name": agent_name,
                    }
                    all_findings_flat.append(entry)
                    active_sigs.add(_fsig(f.file, f.category, agent_name))
                    # Register contributing agents so their dismissals match too
                    for contrib in f.contributing_agents:
                        contrib_name = contrib.get("agent_name", "")
                        if contrib_name and contrib_name != agent_name:
                            active_sigs.add(_fsig(f.file, f.category, contrib_name))

            matched = await match_dismissals_to_findings(
                pr.pr_id,
                pr.repo,
                pr.platform.value,
                all_findings_flat,
            )
            archived = await archive_stale_dismissals(
                pr.pr_id,
                pr.repo,
                pr.platform.value,
                active_sigs,
            )

            # Adjust score: exclude false_positive/by_design from combined score
            score_excluded = {
                sig for sig, d in matched.items() if d["status"] in ("false_positive", "by_design")
            }
            if score_excluded:
                _plog(
                    "info",
                    "dismissals",
                    f"{len(score_excluded)} dismissed finding(s) excluded from score "
                    f"(false_positive/by_design).",
                )

            if matched:
                _plog(
                    "info", "dismissals", f"{len(matched)} finding(s) matched existing dismissals."
                )
            if archived:
                _plog(
                    "info",
                    "dismissals",
                    f"{archived} stale dismissal(s) archived (findings didn't reappear).",
                )

            # Build review diff summary
            prev_finding_sigs = {d.get("signature") for d in dismissals}
            new_sigs = active_sigs - prev_finding_sigs
            resolved_sigs = prev_finding_sigs - active_sigs
            carried_sigs = active_sigs & prev_finding_sigs
            result.dismissal_summary = {
                "new": len(new_sigs),
                "resolved": len(resolved_sigs),
                "carried_over": len(carried_sigs),
                "dismissed": len(matched),
            }
            _plog(
                "info",
                "dismissals",
                f"Review diff: {len(new_sigs)} new, {len(resolved_sigs)} resolved, "
                f"{len(carried_sigs)} carried over, {len(matched)} dismissed.",
            )

        except Exception as e:
            log.warning("dismissal_matching_failed", error=str(e))

    result.pipeline_log = pipeline_log

    # Post results
    side_effects_skipped = skip_platform_side_effects or await _is_stale_automatic_review(
        adapter, pr, storage=storage, review_id=review_db_id
    )
    if post_comment and not side_effects_skipped:
        await _post_results(
            adapter,
            pr,
            result,
            config,
            base_url=base_url,
            comment_mode=comment_mode,
            review_id=review_db_id,
            storage=storage,
            manual_comment_override=manual_comment_override,
        )

    # Persist to DB
    await _save_result(storage, review_db_id, result, _emit)

    log.info(
        "review_complete",
        pr_id=pr.pr_id,
        decision=result.decision.value,
        score=round(result.combined_score, 2),
    )
    return result


async def run_re_review(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    original_review: dict,
    service_config: GuardianConfig | None = None,
    *,
    post_comment: bool = True,
    base_url: str = "",
) -> ReviewResult:
    """Focused re-review: re-evaluate original findings against incremental changes.

    Unlike run_review, this does NOT run the full pipeline. It:
    1. Fetches the incremental diff (old commit → current HEAD)
    2. Collects non-dismissed findings from the original review
    3. Asks each agent to re-evaluate its own findings
    4. Produces a result with kept/resolved/updated findings
    """
    log.info("re_review_started", pr_id=pr.pr_id, repo=pr.repo)

    storage = _try_import_storage()
    review_db_id: uuid.UUID | None = None

    if storage:
        try:
            review_db_id = await storage.create_review_record(
                pr,
                comment_mode=original_review.get("comment_mode", "summary"),
            )
            await storage.set_review_provenance(
                review_db_id,
                profile_id=_uuid_or_none(original_review.get("profile_id")),
                profile_snapshot=original_review.get("profile_snapshot"),
                connection_id=_uuid_or_none(original_review.get("connection_id")),
                connection_snapshot=original_review.get("connection_snapshot"),
                repo_link_id=_uuid_or_none(original_review.get("repo_link_id")),
                candidate_id=_uuid_or_none(original_review.get("candidate_id")),
                review_source="re_review",
            )
        except Exception as e:
            log.warning("db_create_failed", error=str(e))

    pipeline_log: list[dict] = []

    def _plog(level: str, stage: str, msg: str, **extra):
        pipeline_log.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "stage": stage,
                "msg": msg,
                **{k: v for k, v in extra.items() if v is not None},
            }
        )

    def _emit(stage: str, detail: str = "", **extra):
        event_bus.publish(
            ReviewEvent(
                review_id=str(review_db_id) if review_db_id else "",
                pr_id=pr.pr_id,
                repo=pr.repo,
                stage=stage,
                detail=detail,
                extra=extra,
            )
        )

    async def _update_stage(stage: str, detail: str = ""):
        _emit(stage, detail)
        if storage and review_db_id:
            try:
                await storage.update_review_stage(review_db_id, stage, detail)
            except Exception:
                pass

    if post_comment:
        await _post_review_status(adapter, pr, "pending", "Guardian re-review in progress")

    try:
        return await _run_re_review_pipeline(
            pr,
            adapter,
            original_review,
            service_config,
            storage,
            review_db_id,
            pipeline_log,
            _plog,
            _emit,
            _update_stage,
            post_comment=post_comment,
            base_url=base_url,
        )
    except Exception as exc:
        if storage and review_db_id:
            try:
                await storage.mark_review_failed(review_db_id, str(exc), pipeline_log=pipeline_log)
            except Exception:
                pass
        _emit("error", str(exc))
        raise


async def _run_re_review_pipeline(
    pr: PlatformPR,
    adapter: PlatformAdapter,
    original_review: dict,
    service_config: GuardianConfig | None,
    storage,
    review_db_id: uuid.UUID | None,
    pipeline_log: list[dict],
    _plog,
    _emit,
    _update_stage,
    *,
    post_comment: bool = True,
    base_url: str = "",
) -> ReviewResult:
    """Inner re-review pipeline."""
    from pr_guardian.models.context import RiskTier

    if service_config is None:
        config = (
            await resolve_profile_snapshot_config(
                original_review.get("profile_snapshot"),
                original_review.get("connection_snapshot"),
            )
        ).config
    else:
        config = await apply_global_settings(service_config)

    # --- Step 1: Fetch incremental diff ---
    await _update_stage("discovery", "Fetching incremental diff since last review")

    old_sha = original_review.get("head_commit_sha", "")
    new_sha = pr.head_commit_sha

    incremental_diff_text = ""
    if old_sha and new_sha and old_sha != new_sha:
        try:
            incr_diff = await adapter.fetch_compare_diff(
                pr.repo,
                old_sha,
                new_sha,
                project=getattr(pr, "project", "") or "",
            )
            # Build a text representation of the incremental diff
            parts = []
            for f in incr_diff.files:
                parts.append(f"### {f.path} ({f.status})")
                if f.patch:
                    parts.append(f"```\n{f.patch}\n```")
                else:
                    parts.append("*[no patch content]*")
            incremental_diff_text = "\n".join(parts)
            _plog(
                "info",
                "discovery",
                f"Incremental diff: {len(incr_diff.files)} file(s) changed "
                f"between {old_sha[:8]} and {new_sha[:8]}.",
            )
        except Exception as e:
            _plog(
                "warn",
                "discovery",
                f"Could not fetch incremental diff: {e}. "
                f"Re-evaluating findings without new code context.",
            )
    else:
        _plog(
            "info",
            "discovery",
            "No new commits since last review. Re-evaluating findings on their own merits.",
        )

    # --- Step 2: Collect non-dismissed findings grouped by agent ---
    await _update_stage("discovery", "Collecting original findings")

    dismissed_sigs: set[str] = set()
    if storage:
        try:
            from pr_guardian.persistence.storage import finding_signature

            dismissals = await storage.get_active_dismissals(
                pr.pr_id,
                pr.repo,
                pr.platform.value,
            )
            for d in dismissals:
                dismissed_sigs.add(d["signature"])
        except Exception:
            pass

    agent_findings: dict[str, list[dict]] = {}
    total_original = 0
    total_dismissed = 0

    for agent_result in original_review.get("agent_results", []):
        agent_name = agent_result["agent_name"]
        for f in agent_result.get("findings", []):
            total_original += 1
            from pr_guardian.persistence.storage import finding_signature

            sig = finding_signature(
                f.get("file", ""),
                f.get("category", ""),
                agent_name,
            )
            if sig in dismissed_sigs:
                total_dismissed += 1
                continue
            agent_findings.setdefault(agent_name, []).append(f)

    active_findings = sum(len(fs) for fs in agent_findings.values())
    _plog(
        "info",
        "discovery",
        f"Original review: {total_original} finding(s), "
        f"{total_dismissed} dismissed, {active_findings} to re-evaluate.",
    )

    comment_mode = original_review.get("comment_mode", "summary")
    # Reviews are serialized with an "id" key (see _review_to_dict); "review_id"
    # never existed here, so the old lookup silently disabled stale-comment cleanup.
    original_review_id = original_review.get("id", "")

    if active_findings == 0:
        _plog("info", "decision", "No active findings to re-evaluate. Auto-approving.")
        result = ReviewResult(
            pr_id=pr.pr_id,
            repo=pr.repo,
            risk_tier=RiskTier.TRIVIAL,
            repo_risk_class=_parse_risk_class(original_review.get("repo_risk_class")),
            review_id=str(review_db_id) if review_db_id else "",
            mechanical_results=[],
            mechanical_passed=True,
            decision=Decision.AUTO_APPROVE,
            agent_results=[],
            summary="Re-review: all original findings have been dismissed.",
            pipeline_log=pipeline_log,
        )
        if post_comment:
            await _post_results(
                adapter,
                pr,
                result,
                config,
                base_url=base_url,
                comment_mode=comment_mode,
                review_id=review_db_id,
                storage=storage,
                original_review_id=original_review_id,
            )
        await _save_result(storage, review_db_id, result, _emit)
        return result

    # --- Step 2b: Fetch current file content for files referenced by findings ---
    # This ensures the agent can see the actual code at HEAD, even when the
    # incremental diff has no patch content (ADO) or the compare API failed
    # (e.g. force-push rewrote old_sha out of existence).
    await _update_stage("discovery", "Fetching current file content for finding locations")

    finding_files: set[str] = set()
    for fs in agent_findings.values():
        for finding in fs:
            file_path = finding.get("file", "")
            if file_path:
                finding_files.add(file_path)

    current_file_contents: dict[str, str] = {}
    if finding_files and new_sha:
        fetch_tasks = []
        file_paths_ordered = sorted(finding_files)
        # ADO's fetch_file_content expects "project/repo" format
        repo_arg = pr.repo
        if getattr(pr, "project", "") and "/" not in pr.repo:
            repo_arg = f"{pr.project}/{pr.repo}"
        for fpath in file_paths_ordered:
            fetch_tasks.append(adapter.fetch_file_content(repo_arg, fpath, ref=new_sha))
        fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        for fpath, content in zip(file_paths_ordered, fetched):
            if isinstance(content, BaseException):
                _plog("debug", "discovery", f"Could not fetch {fpath} at {new_sha[:8]}: {content}")
            elif content:
                current_file_contents[fpath] = content

        _plog(
            "info",
            "discovery",
            f"Fetched current content for {len(current_file_contents)}/{len(finding_files)} finding file(s).",
        )

    # --- Step 3: Run agents in re-evaluation mode ---
    await _update_stage(
        "agents",
        f"Re-evaluating {active_findings} finding(s) across {len(agent_findings)} agent(s)",
    )

    pr_metadata = {
        "title": pr.title,
        "repo": pr.repo,
        "author": pr.author,
    }

    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    re_eval_tasks = []
    agent_names_ordered = []
    for agent_name, findings in agent_findings.items():
        agent_cls = AGENT_REGISTRY.get(agent_name)
        if not agent_cls:
            continue
        agent = agent_cls(config)
        re_eval_tasks.append(
            agent.re_evaluate(
                findings,
                incremental_diff_text,
                pr_metadata,
                current_file_contents=current_file_contents,
            )
        )
        agent_names_ordered.append(agent_name)

    all_evaluations = await asyncio.gather(*re_eval_tasks)

    # --- Step 4: Build results from evaluations ---
    await _update_stage("decision", "Computing final verdict from re-evaluation")

    kept_findings: list[AgentResult] = []

    for agent_name, evaluations in zip(agent_names_ordered, all_evaluations):
        original_findings = agent_findings[agent_name]
        kept: list[Finding] = []
        resolved_count = 0

        for ev in evaluations:
            idx = ev.get("finding_index", 0) - 1
            if idx < 0 or idx >= len(original_findings):
                continue
            status = ev.get("status", "kept")
            orig = original_findings[idx]

            if status == "resolved":
                resolved_count += 1
                _plog(
                    "info",
                    "agents",
                    f"Agent {agent_name}: finding {idx + 1} RESOLVED — {ev.get('reason', '')[:100]}",
                )
                continue

            severity = ev.get("updated_severity") or orig.get("severity", "low")
            description = ev.get("updated_description") or orig.get("description", "")
            if status == "updated":
                _plog(
                    "info",
                    "agents",
                    f"Agent {agent_name}: finding {idx + 1} UPDATED — {ev.get('reason', '')[:100]}",
                )

            kept.append(
                Finding(
                    severity=Severity(severity),
                    certainty=Certainty(orig.get("certainty", "uncertain")),
                    category=orig.get("category", ""),
                    language=orig.get("language", ""),
                    file=orig.get("file", ""),
                    line=orig.get("line"),
                    description=description,
                    suggestion=orig.get("suggestion", ""),
                    cwe=orig.get("cwe"),
                )
            )

        # Track tokens from first evaluation's meta
        if evaluations and evaluations[0].get("_meta"):
            meta = evaluations[0]["_meta"]
            in_tok = meta.get("input_tokens", 0)
            out_tok = meta.get("output_tokens", 0)
            total_input_tokens += in_tok
            total_output_tokens += out_tok
            agent_cost = _estimate_cost(meta.get("model", ""), in_tok, out_tok)
            total_cost += agent_cost

        verdict = (
            Verdict.PASS
            if not kept
            else (
                Verdict.FLAG_HUMAN
                if any(f.severity in (Severity.HIGH, Severity.CRITICAL) for f in kept)
                else Verdict.WARN
            )
        )

        _plog(
            "info", "agents", f"Agent {agent_name}: {len(kept)} kept, {resolved_count} resolved."
        )

        kept_findings.append(
            AgentResult(
                agent_name=agent_name,
                verdict=verdict,
                findings=kept,
            )
        )

    # Compute decision through the shared decision engine so re-review and full
    # review can never diverge. Finding-derived inputs (score, finding reasons)
    # are recomputed from the re-evaluated findings; structural inputs (trust
    # tier, sticky triggers, repo risk, risk tier, target branch) are *replayed*
    # from the original review since re-evaluation does not change them.
    all_kept = sum(len(ar.findings) for ar in kept_findings)
    has_high = any(
        f.severity in (Severity.HIGH, Severity.CRITICAL)
        for ar in kept_findings
        for f in ar.findings
    )

    from pr_guardian.decision.engine import (
        combined_score as calc_score,
        finding_overrides,
        resolve_decision,
    )

    score = calc_score(kept_findings, config) if all_kept > 0 else 0.0

    repo_risk = _parse_risk_class(original_review.get("repo_risk_class"))
    risk_tier = _parse_risk_tier(
        original_review.get("risk_tier"),
        fallback=(
            RiskTier.HIGH if has_high else (RiskTier.MEDIUM if all_kept > 0 else RiskTier.TRIVIAL)
        ),
    )
    trust_tier = _parse_trust_tier(original_review.get("trust_tier"))
    target_branch = original_review.get("target_branch", "") or ""

    # Replay the original structural triggers, minus the trust-tier one —
    # resolve_decision re-adds that from trust_tier so we avoid a duplicate.
    sticky_triggers = _replay_sticky_triggers(original_review.get("sticky_triggers", []))
    finding_reasons = finding_overrides(kept_findings, config)

    decision = resolve_decision(
        risk_tier=risk_tier,
        repo_risk=repo_risk,
        agent_results=kept_findings,
        score=score,
        config=config,
        trust_tier=trust_tier,
        sticky_triggers=sticky_triggers,
        finding_reasons=finding_reasons,
        target_branch=target_branch,
    )

    resolved_count_total = active_findings - all_kept
    if all_kept == 0:
        summary_text = "Re-review: all findings have been resolved."
    else:
        summary_text = f"Re-review: {all_kept} finding(s) remain ({resolved_count_total} resolved)."
        if decision in (Decision.HUMAN_REVIEW, Decision.REJECT, Decision.HARD_BLOCK) and has_high:
            summary_text += " High-severity findings need attention."
    if decision != Decision.AUTO_APPROVE and all_kept == 0:
        # Clean code but a structural gate (e.g. trust tier) still requires a human.
        summary_text = "Re-review: all findings resolved; human approval still required."

    result = ReviewResult(
        pr_id=pr.pr_id,
        repo=pr.repo,
        risk_tier=risk_tier,
        repo_risk_class=_parse_risk_class(original_review.get("repo_risk_class")),
        review_id=str(review_db_id) if review_db_id else "",
        mechanical_results=[],
        mechanical_passed=True,
        decision=decision,
        agent_results=kept_findings,
        combined_score=score,
        summary=summary_text,
        pipeline_log=pipeline_log,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        cost_usd=round(total_cost, 6),
        dismissal_summary={
            "original_findings": total_original,
            "dismissed": total_dismissed,
            "re_evaluated": active_findings,
            "resolved": active_findings - all_kept,
            "kept": all_kept,
        },
    )

    if post_comment:
        await _post_results(
            adapter,
            pr,
            result,
            config,
            base_url=base_url,
            comment_mode=comment_mode,
            review_id=review_db_id,
            storage=storage,
            original_review_id=original_review_id,
        )
    await _save_result(storage, review_db_id, result, _emit)

    log.info(
        "re_review_complete",
        pr_id=pr.pr_id,
        decision=decision.value,
        kept=all_kept,
        resolved=active_findings - all_kept,
    )
    return result


def _parse_risk_class(value: str | None) -> RepoRiskClass:
    """Parse a risk class string, with fallback."""
    mapping = {
        "standard": RepoRiskClass.STANDARD,
        "elevated": RepoRiskClass.ELEVATED,
        "critical": RepoRiskClass.CRITICAL,
    }
    return mapping.get(value or "", RepoRiskClass.STANDARD)


def _parse_risk_tier(value: str | None, *, fallback: RiskTier) -> RiskTier:
    """Parse a stored risk tier string, falling back when absent/unknown."""
    try:
        return RiskTier(value) if value else fallback
    except ValueError:
        return fallback


def _parse_trust_tier(value: str | None) -> TrustTier | None:
    """Parse a stored trust tier string. Empty/unknown means no trust gate."""
    try:
        return TrustTier(value) if value else None
    except ValueError:
        return None


def _replay_sticky_triggers(stored: list) -> list[StickyTrigger]:
    """Rebuild StickyTrigger objects from a stored review's serialized triggers.

    The trust-tier trigger is intentionally dropped: resolve_decision re-derives
    it from the trust tier, so replaying it here would duplicate the audit entry.
    """
    fields = ("kind", "label", "source", "reason")
    out: list[StickyTrigger] = []
    for d in stored or []:
        if not isinstance(d, dict) or d.get("kind") == "trust_tier":
            continue
        try:
            out.append(StickyTrigger(**{k: d.get(k, "") for k in fields}))
        except (TypeError, ValueError):
            continue
    return out


def _uuid_or_none(value) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def _save_result(storage, review_db_id, result, _emit) -> None:
    """Persist the review result and emit the 'complete' event."""
    if storage and review_db_id:
        try:
            await storage.save_review_result(review_db_id, result)
        except Exception as e:
            log.error("db_save_failed", error=str(e))
            # Fallback: at least mark the review as finished so it doesn't
            # appear stuck in the dashboard forever.
            try:
                await storage.mark_review_failed(
                    review_db_id,
                    f"Review completed ({result.decision.value}) but save failed: {e}",
                    pipeline_log=result.pipeline_log,
                )
            except Exception as fallback_err:
                log.error("db_fallback_mark_failed", error=str(fallback_err))
        else:
            try:
                await storage.mark_candidate_reviewed_for_review(review_db_id)
            except Exception as e:
                log.warning("candidate_review_completion_update_failed", error=str(e))
    _emit("complete", f"Decision: {result.decision.value}", score=result.combined_score)


def _convert_mechanical(r) -> MechanicalResult:
    """Convert MechanicalCheckResult to the output model."""
    return MechanicalResult(
        tool=r.tool,
        passed=r.passed,
        severity=r.severity.value if hasattr(r.severity, "value") else str(r.severity),
        findings=[
            {"file": f.file, "line": f.line, "rule": f.rule, "message": f.message}
            for f in r.findings
        ],
        error=r.error,
    )


_MECH_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "info": Severity.LOW,
    "low": Severity.LOW,
}


async def _post_review_status(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    state: str,
    description: str,
    target_url: str = "",
) -> None:
    method = getattr(adapter, "set_review_status", None)
    if method is not None:
        try:
            result = method(pr, state, description, target_url=target_url)
        except TypeError:
            result = method(pr, state, description)
        if inspect.isawaitable(result):
            await result
        return
    try:
        result = adapter.set_status(
            pr,
            state,
            description,
            context="guardian/review",
            target_url=target_url,
        )
    except TypeError:
        result = adapter.set_status(pr, state, description)
    if inspect.isawaitable(result):
        await result


async def _post_result_status(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    result: ReviewResult,
    *,
    base_url: str = "",
) -> None:
    target_url = build_review_detail_url(result.review_id, base_url) or ""
    if result.decision == Decision.AUTO_APPROVE:
        await _post_review_status(adapter, pr, "success", "Guardian cleared", target_url)
    elif result.decision == Decision.HUMAN_REVIEW:
        await _post_review_status(
            adapter, pr, "failure", "Guardian review needs human review", target_url
        )
    elif result.decision == Decision.REJECT:
        await _post_review_status(
            adapter, pr, "failure", "Guardian review requested changes", target_url
        )
    elif result.decision == Decision.HARD_BLOCK:
        await _post_review_status(
            adapter, pr, "failure", "Guardian review blocked this PR", target_url
        )


async def _is_stale_automatic_review(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    *,
    storage,
    review_id: uuid.UUID | None,
) -> bool:
    if not storage or not review_id:
        return False
    try:
        review = await storage.get_review(review_id)
    except Exception as exc:
        log.warning("stale_review_lookup_failed", review_id=str(review_id), error=str(exc))
        return False
    if not review or review.get("review_source") != "automatic":
        return False
    expected_sha = review.get("head_commit_sha") or pr.head_commit_sha
    try:
        metadata = await adapter.fetch_pr_metadata(pr)
    except Exception as exc:
        log.warning("stale_sha_metadata_lookup_failed", pr_id=pr.pr_id, error=str(exc))
        return False
    if metadata.head_sha and expected_sha and metadata.head_sha != expected_sha:
        log.info(
            "stale_automatic_review_side_effects_skipped",
            review_id=str(review_id),
            expected_sha=expected_sha,
            current_sha=metadata.head_sha,
        )
        if review.get("candidate_id"):
            try:
                await storage.record_candidate_transition(
                    uuid.UUID(review["candidate_id"]),
                    to_state="superseded",
                    source="review_completion",
                    actor="guardian",
                    reason="new_commit",
                    readiness_snapshot={
                        "expected_head_sha": expected_sha,
                        "current_head_sha": metadata.head_sha,
                    },
                )
            except Exception as exc:
                log.warning("candidate_supersede_failed", review_id=str(review_id), error=str(exc))
        return True
    return False


async def _post_results(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    result: ReviewResult,
    config: GuardianConfig,
    *,
    base_url: str = "",
    comment_mode: str = "summary",
    review_id: uuid.UUID | None = None,
    storage=None,
    original_review_id: str | None = None,
    manual_comment_override: bool = False,
) -> None:
    """Post review results back to the platform."""
    comment = build_summary_comment(result, base_url=base_url)
    labels = get_review_labels(result)
    comments_enabled = config.side_effects.comments or manual_comment_override
    inline_enabled = comments_enabled and comment_mode == "inline"
    summary_enabled = comments_enabled and comment_mode == "summary"

    postback: dict = {}
    review_url = build_review_detail_url(result.review_id, base_url) or ""

    # guardian/review status
    status_state = ""
    try:
        await _post_result_status(adapter, pr, result, base_url=base_url)
        status_state = _decision_to_status_state(result)
        postback["status_posted"] = True
        postback["status_state"] = status_state
    except Exception as e:
        log.warning("post_review_status_failed", pr_id=pr.pr_id, error=str(e))
        postback["status_posted"] = False

    if inline_enabled:
        await _post_inline_and_summary(
            adapter,
            pr,
            result,
            config,
            comment,
            labels,
            review_id=review_id,
            storage=storage,
            original_review_id=original_review_id,
            post_summary=comments_enabled,
            labels_enabled=config.side_effects.labels,
            postback=postback,
        )
        try:
            await _apply_platform_actions(adapter, pr, result, config, comment, postback=postback)
        except Exception as e:
            log.error("post_results_failed", pr_id=pr.pr_id, error=str(e))
    else:
        try:
            if summary_enabled:
                await adapter.post_comment(pr, comment)
            if config.side_effects.labels:
                for label in labels:
                    await adapter.add_label(pr, label)
            await _apply_platform_actions(adapter, pr, result, config, comment, postback=postback)
        except Exception as e:
            log.error("post_results_failed", pr_id=pr.pr_id, error=str(e))

    # Sticky guidance comment — always attempted for adapters that support it
    guidance_state = _decision_to_guidance_state(result)
    comment_id = await _upsert_guidance_comment(
        adapter,
        pr,
        guidance_state,
        review_url=review_url,
        storage=storage,
    )
    if comment_id is not None:
        postback["guidance_comment_id"] = comment_id
        postback["guidance_posted"] = True
    elif getattr(adapter, "upsert_guidance_comment", None) is not None:
        postback["guidance_posted"] = False

    result.postback_meta = postback


def _decision_to_status_state(result: ReviewResult) -> str:
    # Delegates to _decision_to_guidance_state; kept separate so the postback
    # panel label and the guidance comment state can diverge independently later.
    return _decision_to_guidance_state(result)


def _decision_to_guidance_state(result: ReviewResult) -> str:
    if result.decision == Decision.AUTO_APPROVE:
        return "success"
    if result.decision == Decision.HARD_BLOCK:
        return "blocked"
    return "failure"


async def _apply_platform_actions(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    result: ReviewResult,
    config: GuardianConfig,
    comment: str,
    *,
    postback: dict | None = None,
) -> None:
    """Apply non-status platform actions based on decision and Profile switches."""
    if postback is None:
        postback = {}
    if result.decision == Decision.AUTO_APPROVE:
        if config.platform_approval_enabled and config.side_effects.formal_approve:
            fork: bool | None = None
            try:
                fork = (await adapter.fetch_pr_metadata(pr)).fork
            except Exception as exc:
                log.warning("fork_metadata_lookup_failed", pr_id=pr.pr_id, error=str(exc))
            if fork is False:
                await adapter.approve_pr(pr)
                postback["formal_approval"] = "posted"
            elif fork is True:
                postback["formal_approval"] = "skipped_fork"
            else:
                postback["formal_approval"] = "skipped_fork_unknown"
        else:
            postback["formal_approval"] = "skipped_profile"
        if (
            config.side_effects.reviewers
            and result.trust_tier
            and result.trust_tier.value == "spot_check"
        ):
            await adapter.request_reviewers(pr, config.human_review.reviewer_group)
    elif result.decision == Decision.REJECT:
        if config.side_effects.formal_request_changes:
            await adapter.request_changes(pr, comment)
    elif result.decision == Decision.HUMAN_REVIEW:
        if config.side_effects.reviewers:
            reviewer_group = result.reviewer_group_override or config.human_review.reviewer_group
            await adapter.request_reviewers(pr, reviewer_group)
    elif result.decision == Decision.HARD_BLOCK and config.side_effects.formal_request_changes:
        await adapter.request_changes(pr, comment)


async def _post_inline_and_summary(
    adapter: PlatformAdapter,
    pr: PlatformPR,
    result: ReviewResult,
    config: GuardianConfig,
    comment: str,
    labels: list[str],
    *,
    review_id: uuid.UUID | None,
    storage,
    original_review_id: str | None,
    post_summary: bool = True,
    labels_enabled: bool = True,
    postback: dict | None = None,
) -> None:
    """Handle inline comment mode: delete old, post inline, then post summary."""
    if postback is None:
        postback = {}
    threshold = config.inline_comments.severity_threshold.lower()
    threshold_ord = SEVERITY_ORDER.get(threshold, SEVERITY_ORDER["medium"])

    # Collect qualifying findings from agent results. Stamp primary_agent with
    # the AgentResult's name (the value the re-review dismissal filter matches on)
    # so a reply-to-comment dismissal resolves to the right signature.
    qualifying: list[Finding] = []
    for ar in result.agent_results:
        for f in ar.findings:
            if f.line is not None and SEVERITY_ORDER.get(f.severity.value, 0) >= threshold_ord:
                if not f.primary_agent:
                    f.primary_agent = ar.agent_name
                qualifying.append(f)

    # Collect qualifying findings from mechanical results
    for mech in result.mechanical_results:
        tool_sev = _MECH_SEVERITY_MAP.get(mech.severity.lower(), Severity.LOW)
        if SEVERITY_ORDER.get(tool_sev.value, 0) < threshold_ord:
            continue
        for f_dict in mech.findings:
            line = f_dict.get("line")
            if line is None:
                continue
            qualifying.append(
                Finding(
                    severity=tool_sev,
                    certainty=Certainty.DETECTED,
                    category=f_dict.get("rule", mech.tool),
                    language="",
                    file=f_dict.get("file", ""),
                    line=line,
                    description=f_dict.get("message", ""),
                )
            )

    # Delete stale inline comments from the previous review (re-review path)
    if original_review_id and storage:
        try:
            old_ids = await storage.load_inline_comment_ids(uuid.UUID(original_review_id))
            if old_ids:
                await adapter.delete_inline_comments(pr, old_ids)
        except Exception as e:
            log.warning("delete_inline_comments_failed", pr_id=pr.pr_id, error=str(e))

    # Post new inline comments
    if qualifying:
        try:
            inline_result = await adapter.post_inline_comments(pr, qualifying)
            if inline_result.posted_ids and storage and review_id:
                await storage.save_inline_comment_ids(
                    review_id,
                    inline_result.posted_ids,
                    pr.platform.value,
                    pr.pr_id,
                    pr.repo,
                    id_to_findings=inline_result.id_to_findings,
                )
            postback["inline_comments_posted"] = len(inline_result.posted_ids)
            if inline_result.skipped:
                log.warning(
                    "inline_comments_not_anchored",
                    pr_id=pr.pr_id,
                    count=len(inline_result.skipped),
                )
        except Exception as e:
            log.error("post_inline_comments_failed", pr_id=pr.pr_id, error=str(e))
            postback["inline_comments_posted"] = 0
    else:
        postback["inline_comments_posted"] = 0

    # Summary comment posts last in inline mode when comments are enabled.
    try:
        if post_summary:
            await adapter.post_comment(pr, comment)
        if labels_enabled:
            for label in labels:
                await adapter.add_label(pr, label)
    except Exception as e:
        log.error("post_results_failed", pr_id=pr.pr_id, error=str(e))
