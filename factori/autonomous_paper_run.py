"""Fail-closed one-command autonomous paper finalization controller."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.autonomous_loop import (
    AutonomousLoopError,
    latest_autonomous_loop_report,
    run_autonomous_loop,
)
from factori.autonomous_paper_checkpoint import (
    AutonomousPaperCheckpointVerification,
    verify_autonomous_paper_checkpoints,
    write_autonomous_paper_checkpoint,
    write_autonomous_paper_resume_report,
)
from factori.claim_evidence import latest_claim_evidence_map_path
from factori.final_bundle_verification import (
    FinalBundleVerificationError,
    verify_final_release_bundle,
    write_final_bundle_verification_report,
)
from factori.final_manuscript_regeneration import (
    FinalManuscriptRegenerationError,
    latest_final_manuscript_regeneration,
    regenerate_final_manuscript,
)
from factori.final_release_bundle import (
    FinalReleaseBundleError,
    build_final_release_bundle,
    latest_final_release_bundle,
)
from factori.full_paper_generation import (
    inspect_paper_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import evaluate_full_paper_release
from factori.ledger import ResearchLedger
from factori.llm_orchestration import LLMOrchestrationError, run_llm_paper_orchestration
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousPaperResumeReport,
    AutonomousPaperRunHandoff,
    AutonomousPaperRunIndex,
    AutonomousPaperRunReport,
    AutonomousPaperRunStage,
    ClaimEvidenceMap,
    ControllerActionType,
    FullPaperReleaseGateConfig,
    LLMOrchestrationConfig,
)
from factori.storage_protocols import Clock, SystemClock


class AutonomousPaperRunError(RuntimeError):
    """Raised when an autonomous paper controller cannot start or persist safely."""


class AutonomousPaperInjectedCrash(AutonomousPaperRunError):
    """Deterministic test-only crash after a durable controller checkpoint."""


@dataclass(frozen=True)
class AutonomousPaperRunResult:
    """Persisted result of one autonomous finalization controller run."""

    run_id: str
    report: AutonomousPaperRunReport
    index: AutonomousPaperRunIndex
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef


@dataclass
class _ControllerState:
    run_id: str
    autonomous_run_id: str
    domain: str
    topic: str | None
    controller_backend: str
    started_at: str
    stages: list[AutonomousPaperRunStage] = field(default_factory=list)
    base_generation_status: str | None = None
    autonomous_loop_status: str | None = None
    final_manuscript_status: str | None = None
    final_bundle_status: str | None = None
    final_bundle_verification_status: str | None = None
    final_bundle_path: str | None = None
    final_verification_report_path: str | None = None
    final_manuscript_path: str | None = None
    release_report_path: str | None = None
    claim_evidence_map_path: str | None = None
    deferred_gap_count: int = 0
    unsupported_claim_count: int = 0
    missing_citation_count: int = 0
    citation_validation_misuse_count: int = 0
    root_base_generation_failure_stage: str | None = None
    root_base_generation_failure_reason: str | None = None
    candidate_count: int = 0
    stage_a_survivor_count: int = 0
    stage_b_survivor_count: int = 0
    stage_c_ready_count: int = 0
    manuscript_plan_present: bool = False
    budget_blocked_component_optional: str | None = None
    network_used: bool = False
    external_api_used: bool = False
    external_tools_used: bool = False
    checkpoint_session: _CheckpointSession | None = None
    resume_verification: AutonomousPaperCheckpointVerification | None = None
    resume_started_at: str | None = None
    actual_resume_stage: str = "not_requested"
    stages_reused: list[str] = field(default_factory=list)
    stages_rerun: list[str] = field(default_factory=list)


@dataclass
class _CheckpointSession:
    run_id: str
    controller_run_id: str
    root: Path
    store: ArtifactStore
    ledger: ResearchLedger
    clock: Clock
    fault_after_stage: str | None = None
    previous_checkpoint_hash: str | None = None

    def record(
        self,
        stage: AutonomousPaperRunStage,
        artifact_paths: list[str],
        *,
        safety_gate_status: str,
        release_status: str | None = None,
    ) -> None:
        input_hashes = (
            {"previous_checkpoint": self.previous_checkpoint_hash}
            if self.previous_checkpoint_hash
            else {}
        )
        result = write_autonomous_paper_checkpoint(
            run_id=self.run_id,
            controller_run_id=self.controller_run_id,
            stage=stage,
            artifact_paths=artifact_paths,
            safety_gate_status=safety_gate_status,
            release_status=release_status,
            input_hashes=input_hashes,
            root=self.root,
            store=self.store,
            ledger=self.ledger,
            clock=self.clock,
        )
        self.previous_checkpoint_hash = result.checkpoint.checkpoint_hash
        if self.fault_after_stage == stage.stage_name:
            raise AutonomousPaperInjectedCrash(
                f"Injected crash after durable {stage.stage_name} checkpoint."
            )


_DOWNSTREAM_STAGES = (
    "autonomous_loop",
    "final_manuscript_regeneration",
    "final_release_bundle_assembly",
    "final_bundle_verification",
)


def run_autonomous_paper(
    *,
    config: LLMOrchestrationConfig,
    root: str | Path = ".",
    controller_backend: str = "deterministic",
    llm_scope: str = "full-paper",
    enable_safe_repair: bool = True,
    loop_backend: str = "deterministic",
    max_loop_iterations: int = 6,
    max_attempts_per_gap: int = 1,
    enable_strategy_diversification: bool = False,
    enable_experiment_routing: bool = False,
    enable_empirical_demonstration_gaps: bool = False,
    enable_capability_escalation: bool = False,
    python_sandbox_backend: str = "off",
    max_sandbox_runs_per_loop: int = 2,
    max_sandbox_runs_per_iteration: int = 1,
    regeneration_backend: str = "deterministic",
    build_final_bundle: bool = True,
    verify_final_bundle: bool = True,
    compile_pdf: bool = False,
    strict_export: bool = False,
    resume_existing: bool = False,
    fault_after_stage: str | None = None,
    clock: Clock | None = None,
) -> AutonomousPaperRunResult:
    """Run the complete autonomous MVP and persist one bounded handoff report."""
    if controller_backend != "deterministic":
        raise AutonomousPaperRunError(
            "Only the deterministic autonomous paper controller is implemented."
        )
    if llm_scope != "full-paper":
        raise AutonomousPaperRunError(
            "run-autonomous-paper requires llm_scope=full-paper for finalization."
        )
    root_path = Path(root)
    run_path = root_path / "runs" / config.run_id
    if run_path.exists() and not resume_existing:
        raise AutonomousPaperRunError(
            f"Run already exists for run_id={config.run_id}; autonomous finalization is "
            "append-only and will not overwrite it."
        )
    if resume_existing and not run_path.is_dir():
        raise AutonomousPaperRunError(
            f"Cannot resume missing run_id={config.run_id}; create it without --resume-existing."
        )

    clock = clock or SystemClock()
    store = ArtifactStore(root_path)
    if resume_existing:
        store.validate_run_structure(config.run_id)
    else:
        store.init_run(config.run_id)
    ledger = ResearchLedger(run_path / "ledger.sqlite", clock=clock)
    reports = run_path / "reports"
    controller_number = len(
        [
            path
            for path in reports.glob("autonomous-paper-run-[0-9][0-9][0-9][0-9].json")
            if not path.name.endswith(".meta.json")
        ]
    ) + 1
    controller_run_id = f"autonomous-paper-run-{controller_number:04d}"
    resume_verification: AutonomousPaperCheckpointVerification | None = None
    reusable_stages: list[str] = []
    actual_resume_stage = "not_requested"
    if resume_existing:
        resume_verification, reusable_stages, actual_resume_stage = _prepare_resume(
            run_id=config.run_id,
            root=root_path,
        )
    state = _ControllerState(
        run_id=config.run_id,
        autonomous_run_id=controller_run_id,
        domain=config.domain,
        topic=config.method,
        controller_backend=controller_backend,
        started_at=clock.now(),
        resume_verification=resume_verification,
        resume_started_at=clock.now() if resume_existing else None,
        actual_resume_stage=actual_resume_stage,
        stages_reused=list(reusable_stages),
    )
    state.checkpoint_session = _CheckpointSession(
        run_id=config.run_id,
        controller_run_id=controller_run_id,
        root=root_path,
        store=store,
        ledger=ledger,
        clock=clock,
        fault_after_stage=fault_after_stage,
        previous_checkpoint_hash=(
            resume_verification.checkpoints[-1].checkpoint_hash
            if resume_verification and resume_verification.checkpoints
            else None
        ),
    )
    effective_config = config.model_copy(
        update={
            "generate_paper": True,
            "evaluate_release": True,
            "export_latex": True,
            "write_report": True,
            "rerun_policy": "fail-if-exists",
            "force": False,
        }
    )

    base_warnings: list[str] = []
    if "base_generation" in reusable_stages:
        checkpoint = _resume_checkpoint(state, "base_generation")
        state.base_generation_status = checkpoint.release_status_optional or "reused"
        state.network_used, state.external_api_used = _base_external_usage(root_path, config.run_id)
        state.stages.append(_reused_stage(checkpoint, clock))
    else:
        state.stages_rerun.append("base_generation")
        base_started = clock.now()
        try:
            base = run_llm_paper_orchestration(
                config=effective_config,
                root=root_path,
                store=store,
                ledger=ledger,
                clock=clock,
                llm_scope=llm_scope,
                enable_safe_repair=enable_safe_repair,
            )
        except LLMOrchestrationError as exc:
            state.base_generation_status = "failed"
            _apply_base_generation_exception_diagnostics(state, str(exc))
            state.stages.append(
                _stage(
                    "base_generation",
                    "failed",
                    base_started,
                    clock.now(),
                    "Base paper generation failed closed.",
                    blocking=[str(exc)],
                )
            )
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="failed",
                handoff_status="handoff_failed",
                reason=f"Base generation failed: {exc}",
                remaining_stages=_DOWNSTREAM_STAGES,
                human_intervention=True,
            )

        base_status = _value(base.report.orchestration_status)
        state.base_generation_status = base_status
        state.network_used = any(
            item.external_call_performed for item in base.report.call_accounting
        )
        state.external_api_used = state.network_used
        base_warnings = list(base.report.warnings)
        base_artifacts = _artifact_paths(
            base.config_artifact,
            base.budget_artifact,
            base.accounting_artifact,
            base.report_artifact,
            base.safety_artifact,
            base.generation_result.report_artifact if base.generation_result else None,
            base.generation_result.bundle_artifact if base.generation_result else None,
            base.release_result.report_artifact if base.release_result else None,
            base.release_result.summary_artifact if base.release_result else None,
            base.release_result.reviewer_summary_artifact if base.release_result else None,
        )
        base_blocking = list(base.report.blocking_issues)
        _apply_base_generation_diagnostics(
            state,
            root=root_path,
            report=base.report,
            candidate_backend=effective_config.candidate_backend,
        )
        base_safe = (
            base.report.publication_ready is False
            and base.report.safety_report.safe
            and not base_blocking
            and base.generation_result is not None
            and base.release_result is not None
            and base.report.release_status
            in {"ReadyForHumanReview", "ReadyForHumanReviewWithWarnings"}
        )
        state.stages.append(
            _stage(
                "base_generation",
                (
                    "completed_with_warnings"
                    if base_safe and base.report.warnings
                    else "completed"
                    if base_safe
                    else "blocked"
                ),
                base_started,
                clock.now(),
                "Generated and release-checked the base paper package.",
                artifacts=base_artifacts,
                blocking=(
                    _base_stage_blocking_issues(state, base_blocking, base_safe, base_status)
                ),
                warnings=base_warnings,
            )
        )
        if not base_safe:
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_safety_gate",
                handoff_status="handoff_blocked_by_safety_gate",
                reason=(
                    "Base generation did not produce a safety-clean human-review release state."
                ),
                remaining_stages=_DOWNSTREAM_STAGES,
                human_intervention=True,
            )
        _checkpoint_stage(
            state,
            state.stages[-1],
            base_artifacts,
            release_status=base.report.release_status,
        )

    if "autonomous_loop" in reusable_stages:
        checkpoint = _resume_checkpoint(state, "autonomous_loop")
        loop_report, _ = latest_autonomous_loop_report(root_path, config.run_id)
        if loop_report is None:  # Defensive: semantic resume verification already checked this.
            raise AutonomousPaperRunError("Verified autonomous loop checkpoint became unreadable.")
        state.autonomous_loop_status = loop_report.loop_status
        state.deferred_gap_count = loop_report.deferred_gap_count
        state.unsupported_claim_count = int(
            loop_report.final_claim_evidence_counts.get("unsupported", 0)
        )
        state.stages.append(_reused_stage(checkpoint, clock))
    else:
        state.stages_rerun.append("autonomous_loop")
        loop_started = clock.now()
        try:
            loop = run_autonomous_loop(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                loop_backend=loop_backend,
                max_iterations=max_loop_iterations,
                max_attempts_per_gap=max_attempts_per_gap,
                enable_strategy_diversification=enable_strategy_diversification,
                enable_experiment_routing=enable_experiment_routing,
                enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
                enable_capability_escalation=enable_capability_escalation,
                python_sandbox_backend=python_sandbox_backend,
                max_sandbox_runs_per_loop=max_sandbox_runs_per_loop,
                max_sandbox_runs_per_iteration=max_sandbox_runs_per_iteration,
            )
        except AutonomousLoopError as exc:
            state.autonomous_loop_status = "failed"
            state.stages.append(
                _stage(
                    "autonomous_loop",
                    "failed",
                    loop_started,
                    clock.now(),
                    "Autonomous evidence loop failed closed.",
                    blocking=[str(exc)],
                )
            )
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_safety_gate",
                handoff_status="handoff_blocked_by_safety_gate",
                reason=f"Autonomous loop failed: {exc}",
                remaining_stages=_DOWNSTREAM_STAGES[1:],
                human_intervention=True,
            )

        state.autonomous_loop_status = loop.report.loop_status
        state.deferred_gap_count = loop.report.deferred_gap_count
        state.unsupported_claim_count = int(
            loop.report.final_claim_evidence_counts.get("unsupported", 0)
        )
        loop_safe = (
            not loop.report.requires_human_intervention
            and loop.report.final_publication_ready is False
            and loop.report.blocking_gap_count == 0
            and state.unsupported_claim_count == 0
        )
        loop_artifacts = [loop.report_artifact.path, loop.index_artifact.path]
        state.stages.append(
            _stage(
                "autonomous_loop",
                (
                    "completed_with_warnings"
                    if loop_safe and state.deferred_gap_count
                    else "completed"
                ),
                loop_started,
                clock.now(),
                "Completed autonomous evidence planning, execution, and terminal classification.",
                artifacts=loop_artifacts,
                blocking=[] if loop_safe else [loop.report.terminal_state_reason],
                warnings=(
                    [f"{state.deferred_gap_count} deferred gap(s) remain visible."]
                    if loop.report.gap_terminal_classifications
                    else []
                ),
            )
        )
        if not loop_safe:
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_safety_gate",
                handoff_status="handoff_blocked_by_safety_gate",
                reason="Autonomous loop ended with a blocking, corrupt, or unsupported state.",
                remaining_stages=_DOWNSTREAM_STAGES[1:],
                human_intervention=loop.report.requires_human_intervention,
                human_reason=loop.report.human_intervention_reason_optional,
            )
        _checkpoint_stage(state, state.stages[-1], loop_artifacts)

    if "final_manuscript_regeneration" in reusable_stages:
        checkpoint = _resume_checkpoint(state, "final_manuscript_regeneration")
        manuscript_report, _ = latest_final_manuscript_regeneration(root_path, config.run_id)
        if manuscript_report is None:
            raise AutonomousPaperRunError(
                "Verified final manuscript checkpoint became unreadable."
            )
        state.final_manuscript_status = manuscript_report.regeneration_status
        state.final_manuscript_path = manuscript_report.final_manuscript_path
        state.deferred_gap_count = manuscript_report.deferred_gap_count
        state.unsupported_claim_count = manuscript_report.unsupported_claim_count
        state.stages.append(_reused_stage(checkpoint, clock))
    else:
        state.stages_rerun.append("final_manuscript_regeneration")
        manuscript_started = clock.now()
        try:
            manuscript = regenerate_final_manuscript(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                backend=regeneration_backend,
                allow_external_calls=effective_config.allow_external_calls,
            )
        except FinalManuscriptRegenerationError as exc:
            state.final_manuscript_status = "failed"
            state.stages.append(
                _stage(
                    "final_manuscript_regeneration",
                    "failed",
                    manuscript_started,
                    clock.now(),
                    "Final manuscript regeneration failed closed.",
                    blocking=[str(exc)],
                )
            )
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="failed",
                handoff_status="handoff_failed",
                reason=f"Final manuscript regeneration failed: {exc}",
                remaining_stages=_DOWNSTREAM_STAGES[2:],
                human_intervention=True,
            )

        state.final_manuscript_status = manuscript.report.regeneration_status
        state.final_manuscript_path = manuscript.report.final_manuscript_path
        state.deferred_gap_count = manuscript.report.deferred_gap_count
        state.unsupported_claim_count = manuscript.report.unsupported_claim_count
        manuscript_safe = _final_manuscript_is_safe(manuscript.report)
        manuscript_artifacts = [
            manuscript.report.final_manuscript_path,
            manuscript.report.final_manuscript_structured_path,
            manuscript.report_artifact.path,
            manuscript.index_artifact.path,
        ]
        state.stages.append(
            _stage(
                "final_manuscript_regeneration",
                (
                    "completed_with_warnings"
                    if manuscript_safe and state.deferred_gap_count
                    else "completed"
                ),
                manuscript_started,
                clock.now(),
                "Regenerated a coherent manuscript from the final scoped evidence state.",
                artifacts=manuscript_artifacts,
                blocking=(
                    [] if manuscript_safe else ["Final manuscript safety rechecks did not pass."]
                ),
            )
        )
        if not manuscript_safe:
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_safety_gate",
                handoff_status="handoff_blocked_by_safety_gate",
                reason="Final manuscript regeneration did not preserve all safety gates.",
                remaining_stages=_DOWNSTREAM_STAGES[2:],
                human_intervention=True,
            )
        _checkpoint_stage(state, state.stages[-1], manuscript_artifacts)

    if "final_release_bundle_assembly" in reusable_stages:
        checkpoint = _resume_checkpoint(state, "final_release_bundle_assembly")
        bundle_report, _ = latest_final_release_bundle(root_path, config.run_id)
        if bundle_report is None:
            raise AutonomousPaperRunError("Verified final bundle checkpoint became unreadable.")
        state.final_bundle_status = bundle_report.bundle_status
        state.final_bundle_path = bundle_report.bundle_path
        state.release_report_path = bundle_report.release_report_path
        state.claim_evidence_map_path = bundle_report.claim_evidence_map_path
        state.stages.append(_reused_stage(checkpoint, clock))
    else:
        state.stages_rerun.append("final_release_bundle_assembly")
        bundle_started = clock.now()
        if not build_final_bundle:
            state.final_bundle_status = "skipped"
            state.stages.append(
                _stage(
                    "final_release_bundle_assembly",
                    "blocked",
                    bundle_started,
                    clock.now(),
                    "Final bundle assembly was disabled; handoff cannot proceed.",
                    blocking=["Final release bundle is required."],
                )
            )
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_bundle_verification",
                handoff_status="handoff_blocked_by_bundle_verification",
                reason="Final release bundle assembly is required for handoff.",
                remaining_stages=("final_bundle_verification",),
            )
        try:
            bundle = build_final_release_bundle(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                compile_pdf=compile_pdf,
                strict_export=strict_export,
            )
        except FinalReleaseBundleError as exc:
            state.final_bundle_status = "failed"
            state.stages.append(
                _stage(
                    "final_release_bundle_assembly",
                    "failed",
                    bundle_started,
                    clock.now(),
                    "Final release bundle assembly failed closed.",
                    blocking=[str(exc)],
                )
            )
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_bundle_verification",
                handoff_status="handoff_blocked_by_bundle_verification",
                reason=f"Final release bundle assembly failed: {exc}",
                remaining_stages=("final_bundle_verification",),
            )

        bundle_report = bundle.report
        state.final_bundle_status = bundle_report.bundle_status
        state.final_bundle_path = bundle_report.bundle_path
        state.release_report_path = bundle_report.release_report_path
        state.claim_evidence_map_path = bundle_report.claim_evidence_map_path
        bundle_complete = _final_bundle_is_complete(bundle_report)
        bundle_artifacts = [
            bundle.report_artifact.path,
            bundle.index_artifact.path,
            bundle_report.manifest_path,
            bundle_report.reproducibility_manifest_path,
            f"{bundle_report.bundle_path}/reproducibility/hashes.sha256",
        ]
        state.stages.append(
            _stage(
                "final_release_bundle_assembly",
                "completed" if bundle_complete else "blocked",
                bundle_started,
                clock.now(),
                "Assembled and hash-locked the final release bundle.",
                artifacts=bundle_artifacts,
                blocking=list(bundle_report.missing_required_artifacts),
            )
        )
        if not bundle_complete:
            return _finish(
                state,
                root_path,
                store,
                ledger,
                clock,
                controller_status="blocked_bundle_verification",
                handoff_status="handoff_blocked_by_bundle_verification",
                reason="Final release bundle is incomplete.",
                remaining_stages=("final_bundle_verification",),
            )
        _checkpoint_stage(state, state.stages[-1], bundle_artifacts)

    verification_started = clock.now()
    if resume_existing:
        state.stages_rerun.append("final_bundle_verification")
    if not verify_final_bundle:
        state.final_bundle_verification_status = "skipped"
        state.stages.append(
            _stage(
                "final_bundle_verification",
                "blocked",
                verification_started,
                clock.now(),
                "Independent final bundle verification was disabled.",
                blocking=["Independent final bundle verification is required."],
            )
        )
        return _finish(
            state,
            root_path,
            store,
            ledger,
            clock,
            controller_status="blocked_bundle_verification",
            handoff_status="handoff_blocked_by_bundle_verification",
            reason="Independent final bundle verification is required for handoff.",
        )
    try:
        verification = verify_final_release_bundle(
            bundle_path=root_path / bundle_report.bundle_path,
            root=root_path,
            clock=clock,
        )
    except FinalBundleVerificationError as exc:
        state.final_bundle_verification_status = "failed"
        state.stages.append(
            _stage(
                "final_bundle_verification",
                "failed",
                verification_started,
                clock.now(),
                "Independent final bundle verification failed closed.",
                blocking=[str(exc)],
            )
        )
        return _finish(
            state,
            root_path,
            store,
            ledger,
            clock,
            controller_status="blocked_bundle_verification",
            handoff_status="handoff_blocked_by_bundle_verification",
            reason=f"Final bundle verification failed: {exc}",
        )

    verification_json, _ = write_final_bundle_verification_report(
        verification,
        root=root_path,
    )
    state.final_verification_report_path = verification_json.relative_to(root_path).as_posix()
    state.final_bundle_verification_status = verification.verification_status
    state.external_tools_used = verification.external_tools_used
    verification_safe = _final_verification_is_safe(verification)
    state.stages.append(
        _stage(
            "final_bundle_verification",
            "completed_with_warnings" if verification.checks_warned else "completed",
            verification_started,
            clock.now(),
            "Independently verified final bundle integrity and scoped release authority.",
            artifacts=[state.final_verification_report_path],
            blocking=[] if verification_safe else [verification.verification_status],
            warnings=[
                check.message for check in verification.checks if check.status == "warned"
            ],
        )
    )
    if not verification_safe:
        return _finish(
            state,
            root_path,
            store,
            ledger,
            clock,
            controller_status="blocked_bundle_verification",
            handoff_status="handoff_blocked_by_bundle_verification",
            reason="Independent final bundle verification did not pass.",
        )
    _checkpoint_stage(
        state,
        state.stages[-1],
        [state.final_verification_report_path],
    )

    release_recheck = evaluate_full_paper_release(
        run_id=config.run_id,
        root=root_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=config.run_id),
    )
    if release_recheck.decision.status.value not in {
        "ReadyForHumanReview",
        "ReadyForHumanReviewWithWarnings",
    }:
        return _finish(
            state,
            root_path,
            store,
            ledger,
            clock,
            controller_status="blocked_safety_gate",
            handoff_status="handoff_blocked_by_safety_gate",
            reason="Post-resume release-gate recheck blocked handoff.",
            human_intervention=True,
        )

    lint = lint_paper_bundle_summary(run_id=config.run_id, root=root_path)
    state.missing_citation_count = int(
        lint.get("claim_support_missing_required_citation_count") or 0
    )
    state.citation_validation_misuse_count = int(
        lint.get("citation_as_validation_misuse_count") or 0
    )
    state.unsupported_claim_count = max(
        state.unsupported_claim_count,
        int(lint.get("claim_evidence_unsupported_count") or 0),
    )
    safety_blocked = _lint_has_safety_block(lint, state.unsupported_claim_count)
    if safety_blocked:
        return _finish(
            state,
            root_path,
            store,
            ledger,
            clock,
            controller_status="blocked_safety_gate",
            handoff_status="handoff_blocked_by_safety_gate",
            reason="Post-verification claim or citation safety checks are blocking handoff.",
            human_intervention=True,
        )

    if state.deferred_gap_count:
        controller_status = "completed_with_deferred_gaps"
        handoff_status = "handoff_ready_for_evidence_extension"
        reason = (
            "The verified bundle is ready for bounded evidence extension; deferred proof or "
            "retrieval gaps remain visible."
        )
    elif verification.checks_warned or base_warnings:
        controller_status = "completed_with_warnings"
        handoff_status = "handoff_ready_for_human_review_with_warnings"
        reason = "The verified bundle is ready for human review with nonblocking warnings."
    else:
        controller_status = "completed"
        handoff_status = "handoff_ready_for_human_review_with_warnings"
        reason = "The verified bounded bundle is ready for human review; publication_ready=false."
    return _finish(
        state,
        root_path,
        store,
        ledger,
        clock,
        controller_status=controller_status,
        handoff_status=handoff_status,
        reason=reason,
    )


def inspect_autonomous_paper_run(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect the latest autonomous paper controller report without mutation."""
    report, index = latest_autonomous_paper_run(root, run_id)
    if report is None or index is None:
        raise AutonomousPaperRunError(f"No autonomous paper run found for run_id={run_id}.")
    return {
        **report.model_dump(mode="json"),
        **autonomous_paper_run_summary_fields(report, index),
        "autonomous_paper_run_index": index.model_dump(mode="json"),
    }


def latest_autonomous_paper_run(
    root: str | Path,
    run_id: str,
) -> tuple[AutonomousPaperRunReport | None, AutonomousPaperRunIndex | None]:
    """Load the latest immutable controller report and derived index."""
    reports = Path(root) / "runs" / run_id / "reports"
    numbered = sorted(
        reports.glob("autonomous-paper-run-index-[0-9][0-9][0-9][0-9].json")
    )
    index_path = numbered[-1] if numbered else reports / "autonomous-paper-run-index.json"
    if not index_path.is_file():
        return None, None
    try:
        index = AutonomousPaperRunIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
        report = AutonomousPaperRunReport.model_validate_json(
            (Path(root) / index.latest_report_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def autonomous_paper_run_summary_fields(
    report: AutonomousPaperRunReport | None,
    index: AutonomousPaperRunIndex | None = None,
) -> dict[str, Any]:
    """Return stable paper-inspect and lint fields for autonomous finalization."""
    if report is None:
        return {
            "autonomous_paper_run_present": False,
            "autonomous_paper_run_count": 0,
            "autonomous_paper_controller_status": None,
            "autonomous_paper_handoff_status": None,
            "autonomous_paper_final_bundle_verified": False,
            "autonomous_paper_final_bundle_path": None,
            "autonomous_paper_final_verification_status": None,
            "autonomous_paper_deferred_gap_count": 0,
            "autonomous_paper_unsupported_claim_count": 0,
            "autonomous_paper_human_intervention_required": False,
            "root_base_generation_failure_stage": None,
            "root_base_generation_failure_reason": None,
            "candidate_count": 0,
            "stage_a_survivor_count": 0,
            "stage_b_survivor_count": 0,
            "stage_c_ready_count": 0,
            "manuscript_plan_present": False,
            "budget_blocked_component_optional": None,
        }
    return {
        "autonomous_paper_run_present": True,
        "autonomous_paper_run_count": index.autonomous_run_count if index else 1,
        "autonomous_paper_controller_status": report.controller_status,
        "autonomous_paper_handoff_status": report.handoff_status,
        "autonomous_paper_final_bundle_verified": report.final_bundle_verification_status
        in {"verified", "verified_with_warnings"},
        "autonomous_paper_final_bundle_path": report.final_bundle_path_optional,
        "autonomous_paper_final_verification_status": (
            report.final_bundle_verification_status
        ),
        "autonomous_paper_deferred_gap_count": report.deferred_gap_count,
        "autonomous_paper_unsupported_claim_count": report.unsupported_claim_count,
        "autonomous_paper_human_intervention_required": (
            report.human_intervention_required
        ),
        "root_base_generation_failure_stage": report.root_base_generation_failure_stage,
        "root_base_generation_failure_reason": report.root_base_generation_failure_reason,
        "candidate_count": report.candidate_count,
        "stage_a_survivor_count": report.stage_a_survivor_count,
        "stage_b_survivor_count": report.stage_b_survivor_count,
        "stage_c_ready_count": report.stage_c_ready_count,
        "manuscript_plan_present": report.manuscript_plan_present,
        "budget_blocked_component_optional": report.budget_blocked_component_optional,
    }


def render_autonomous_paper_run_markdown(report: AutonomousPaperRunReport) -> str:
    """Render a concise final handoff report."""
    return "\n".join(
        [
            "# Autonomous Paper Run",
            "",
            f"Run ID: `{report.run_id}`",
            f"Controller status: `{report.controller_status}`",
            f"Handoff status: `{report.handoff_status}`",
            f"Handoff reason: {report.handoff_reason}",
            f"Final bundle: `{report.final_bundle_path_optional or 'none'}`",
            (
                "Final verification: "
                f"`{report.final_bundle_verification_status or 'not_available'}`"
            ),
            f"Deferred gaps: `{report.deferred_gap_count}`",
            f"Unsupported claims: `{report.unsupported_claim_count}`",
            (
                "Missing required citations / citation-as-validation misuse: "
                f"`{report.claim_support_missing_required_citation_count} / "
                f"{report.citation_as_validation_misuse_count}`"
            ),
            (
                "Base-generation root failure: "
                f"`{report.root_base_generation_failure_stage or 'none'}` / "
                f"`{report.root_base_generation_failure_reason or 'none'}`"
            ),
            (
                "Base-generation counts: "
                f"candidates `{report.candidate_count}`, "
                f"Stage A survivors `{report.stage_a_survivor_count}`, "
                f"Stage B survivors `{report.stage_b_survivor_count}`, "
                f"Stage C ready `{report.stage_c_ready_count}`, "
                f"manuscript_plan_present "
                f"`{str(report.manuscript_plan_present).lower()}`"
            ),
            f"Human intervention required: `{str(report.human_intervention_required).lower()}`",
            "",
            "## Stages",
            *[
                f"- `{stage.stage_name}`: `{stage.stage_status}` - {stage.summary}"
                for stage in report.stages
            ],
            "",
            "## Reproducibility",
            f"- network_used: {str(report.network_used).lower()}",
            f"- external_api_used: {str(report.external_api_used).lower()}",
            f"- external_tools_used: {str(report.external_tools_used).lower()}",
            "",
            "## Inspect",
            f"`{report.handoff.next_inspection_command}`",
            "",
            "This controller report is orchestration context only. It does not create scientific "
            "validation, verification evidence, approval, or publication readiness.",
            "",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )


def _finish(
    state: _ControllerState,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    clock: Clock,
    *,
    controller_status: str,
    handoff_status: str,
    reason: str,
    remaining_stages: tuple[str, ...] = (),
    human_intervention: bool = False,
    human_reason: str | None = None,
) -> AutonomousPaperRunResult:
    for stage_name in remaining_stages:
        timestamp = clock.now()
        state.stages.append(
            _stage(
                stage_name,
                "skipped",
                timestamp,
                timestamp,
                "Skipped because an earlier controller stage blocked finalization.",
            )
        )
    handoff_started = clock.now()
    if state.resume_verification is not None:
        state.stages_rerun.append("handoff")
    handoff = AutonomousPaperRunHandoff(
        handoff_status=handoff_status,
        handoff_reason=reason,
        final_bundle_path_optional=state.final_bundle_path,
        final_bundle_verification_status=state.final_bundle_verification_status,
        deferred_gap_count=state.deferred_gap_count,
        unsupported_claim_count=state.unsupported_claim_count,
        human_intervention_required=human_intervention,
        human_intervention_reason_optional=human_reason,
        next_inspection_command=(
            f"factori inspect-final-release-bundle --run-id {state.run_id} --json"
        ),
        publication_ready=False,
    )
    state.stages.append(
        _stage(
            "handoff",
            "blocked" if handoff_status.startswith("handoff_blocked") else "failed"
            if handoff_status == "handoff_failed"
            else "completed_with_warnings",
            handoff_started,
            clock.now(),
            reason,
            blocking=[reason] if handoff_status.startswith("handoff_blocked") else [],
        )
    )
    report = AutonomousPaperRunReport(
        run_id=state.run_id,
        autonomous_run_id=state.autonomous_run_id,
        controller_backend=state.controller_backend,
        controller_status=controller_status,
        started_at=state.started_at,
        completed_at=clock.now(),
        domain=state.domain,
        topic_or_question_optional=state.topic,
        stages=state.stages,
        base_generation_status=state.base_generation_status,
        autonomous_loop_status=state.autonomous_loop_status,
        final_manuscript_status=state.final_manuscript_status,
        final_bundle_status=state.final_bundle_status,
        final_bundle_verification_status=state.final_bundle_verification_status,
        handoff_status=handoff_status,
        handoff_reason=reason,
        handoff=handoff,
        publication_ready=False,
        human_intervention_required=human_intervention,
        human_intervention_reason_optional=human_reason,
        final_bundle_path_optional=state.final_bundle_path,
        final_verification_report_path_optional=state.final_verification_report_path,
        final_manuscript_path_optional=state.final_manuscript_path,
        release_report_path_optional=state.release_report_path,
        claim_evidence_map_path_optional=state.claim_evidence_map_path,
        deferred_gap_count=state.deferred_gap_count,
        unsupported_claim_count=state.unsupported_claim_count,
        claim_support_missing_required_citation_count=state.missing_citation_count,
        citation_as_validation_misuse_count=state.citation_validation_misuse_count,
        root_base_generation_failure_stage=state.root_base_generation_failure_stage,
        root_base_generation_failure_reason=state.root_base_generation_failure_reason,
        candidate_count=state.candidate_count,
        stage_a_survivor_count=state.stage_a_survivor_count,
        stage_b_survivor_count=state.stage_b_survivor_count,
        stage_c_ready_count=state.stage_c_ready_count,
        manuscript_plan_present=state.manuscript_plan_present,
        budget_blocked_component_optional=state.budget_blocked_component_optional,
        network_used=state.network_used,
        external_api_used=state.external_api_used,
        external_tools_used=state.external_tools_used,
    )
    result = _persist_controller_report(report, root, store, ledger, clock)
    _checkpoint_stage(
        state,
        state.stages[-1],
        [
            result.report_artifact.path,
            result.markdown_artifact.path,
            result.index_artifact.path,
        ],
        safety_gate_status=(
            "failed"
            if report.handoff_status.startswith("handoff_blocked")
            or report.handoff_status == "handoff_failed"
            else "passed_with_warnings"
        ),
        release_status=report.handoff_status,
    )
    if state.resume_verification is not None:
        reports = root / "runs" / state.run_id / "reports"
        resume_number = len(
            [
                path
                for path in reports.glob(
                    "autonomous-paper-resume-[0-9][0-9][0-9][0-9].json"
                )
                if not path.name.endswith(".meta.json")
            ]
        ) + 1
        resume_status = (
            "completed"
            if report.controller_status == "completed"
            else "completed_with_warnings"
            if report.controller_status
            in {"completed_with_warnings", "completed_with_deferred_gaps"}
            else "blocked"
        )
        resume_report = AutonomousPaperResumeReport(
            run_id=state.run_id,
            resume_id=f"autonomous-paper-resume-{resume_number:04d}",
            controller_run_id=state.autonomous_run_id,
            requested_resume_stage="automatic",
            actual_resume_stage=state.actual_resume_stage,
            started_at=state.resume_started_at or state.started_at,
            completed_at=clock.now(),
            checkpoints_checked=state.resume_verification.checked_count,
            checkpoints_verified=state.resume_verification.verified_count,
            checkpoints_failed=state.resume_verification.failed_count,
            resume_status=resume_status,
            resume_blockers=[],
            stages_reused=state.stages_reused,
            stages_rerun=state.stages_rerun,
            final_controller_status=report.controller_status,
            final_handoff_status=report.handoff_status,
            final_bundle_verification_rerun=True,
            publication_ready=False,
        )
        write_autonomous_paper_resume_report(
            resume_report,
            root=root,
            store=store,
            ledger=ledger,
            clock=clock,
        )
    return result


def _persist_controller_report(
    report: AutonomousPaperRunReport,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    clock: Clock,
) -> AutonomousPaperRunResult:
    reports = root / "runs" / report.run_id / "reports"
    existing = sorted(reports.glob("autonomous-paper-run-[0-9][0-9][0-9][0-9].json"))
    number = len(existing) + 1
    report_id = f"autonomous-paper-run-{number:04d}"
    index_id = f"autonomous-paper-run-index-{number:04d}"
    index = AutonomousPaperRunIndex(
        run_id=report.run_id,
        latest_autonomous_run_id=report.autonomous_run_id,
        autonomous_run_count=number,
        latest_controller_status=report.controller_status,
        latest_handoff_status=report.handoff_status,
        latest_report_path=f"runs/{report.run_id}/reports/{report_id}.json",
        latest_markdown_path=f"runs/{report.run_id}/reports/{report_id}.md",
        publication_ready=False,
    )
    metadata = {
        "stage": "autonomous_paper_run",
        "artifact_role": "autonomous_finalization_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_autonomous_paper_run_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
            ArtifactWriteSpec(
                index_id,
                ArtifactType.REPORT,
                index,
                "json",
                metadata,
            ),
        ],
        action_type=ControllerActionType.AUTONOMOUS_PAPER_RUN_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "autonomous_run_id": report.autonomous_run_id,
            "controller_status": report.controller_status,
            "handoff_status": report.handoff_status,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
        clock=clock,
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AutonomousPaperRunResult(
        run_id=report.run_id,
        report=report,
        index=index,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
    )


def _stage(
    name: str,
    status: str,
    started_at: str,
    completed_at: str,
    summary: str,
    *,
    artifacts: list[str] | None = None,
    blocking: list[str] | None = None,
    warnings: list[str] | None = None,
) -> AutonomousPaperRunStage:
    return AutonomousPaperRunStage(
        stage_name=name,
        stage_status=status,
        started_at=started_at,
        completed_at=completed_at,
        summary=summary,
        artifact_paths=artifacts or [],
        blocking_issues=[item for item in (blocking or []) if item],
        warnings=warnings or [],
        publication_ready=False,
    )


def _apply_base_generation_diagnostics(
    state: _ControllerState,
    *,
    root: Path,
    report: Any,
    candidate_backend: str,
) -> None:
    diagnostics = _base_generation_diagnostics(
        run_id=state.run_id,
        root=root,
        report=report,
        candidate_backend=candidate_backend,
    )
    state.root_base_generation_failure_stage = diagnostics[
        "root_base_generation_failure_stage"
    ]
    state.root_base_generation_failure_reason = diagnostics[
        "root_base_generation_failure_reason"
    ]
    state.candidate_count = diagnostics["candidate_count"]
    state.stage_a_survivor_count = diagnostics["stage_a_survivor_count"]
    state.stage_b_survivor_count = diagnostics["stage_b_survivor_count"]
    state.stage_c_ready_count = diagnostics["stage_c_ready_count"]
    state.manuscript_plan_present = diagnostics["manuscript_plan_present"]
    state.budget_blocked_component_optional = diagnostics[
        "budget_blocked_component_optional"
    ]


def _apply_base_generation_exception_diagnostics(
    state: _ControllerState,
    message: str,
) -> None:
    normalized = message.casefold()
    if "budget" in normalized or "max_" in normalized:
        state.root_base_generation_failure_stage = "llm_budget"
        state.root_base_generation_failure_reason = "runtime_llm_budget_blocked"
        return
    if "api key" in normalized or "adapter" in normalized or "credential" in normalized:
        state.root_base_generation_failure_stage = "adapter_configuration"
        state.root_base_generation_failure_reason = _slug_reason(message)
        return
    state.root_base_generation_failure_stage = "base_generation"
    state.root_base_generation_failure_reason = _slug_reason(message)


def _base_generation_diagnostics(
    *,
    run_id: str,
    root: Path,
    report: Any | None,
    candidate_backend: str,
) -> dict[str, Any]:
    reports = root / "runs" / run_id / "reports"
    candidate_count = _markdown_summary_count(
        reports / "stage-a-report.md",
        "Generated candidates",
    )
    stage_a_survivor_count = _markdown_summary_count(
        reports / "stage-a-report.md",
        "Passing Stage A gate",
    )
    stage_b_survivor_count = _markdown_summary_count(
        reports / "stage-b-report.md",
        "Passing Stage B",
    )
    stage_c_ready_count = _markdown_summary_count(
        reports / "stage-c-selection-report.md",
        "Stage C ready",
    )
    manuscript_plan_present = (reports / "manuscript-plan.json").is_file()
    blocking = list(getattr(report, "blocking_issues", []) or []) if report is not None else []
    budget_component = _budget_blocked_component(report)
    failure_stage: str | None = None
    failure_reason: str | None = None
    if budget_component:
        failure_stage = "llm_budget"
        failure_reason = "runtime_llm_budget_blocked"
    elif blocking:
        if stage_c_ready_count == 0 and not manuscript_plan_present:
            failure_stage = "stage_c_selection"
            failure_reason = (
                "openai_candidate_generation_produced_no_stage_c_ready_candidates"
                if candidate_backend.strip().lower() == "openai"
                else "no_stage_c_ready_candidates"
            )
        elif any("Quality repair could not complete safely" in item for item in blocking):
            failure_stage = "quality_repair"
            failure_reason = "quality_repair_could_not_complete_safely"
        elif not manuscript_plan_present:
            failure_stage = "manuscript_planning"
            failure_reason = "manuscript_plan_missing"
        else:
            failure_stage = "base_generation"
            failure_reason = _slug_reason(blocking[0])
    return {
        "root_base_generation_failure_stage": failure_stage,
        "root_base_generation_failure_reason": failure_reason,
        "candidate_count": candidate_count,
        "stage_a_survivor_count": stage_a_survivor_count,
        "stage_b_survivor_count": stage_b_survivor_count,
        "stage_c_ready_count": stage_c_ready_count,
        "manuscript_plan_present": manuscript_plan_present,
        "budget_blocked_component_optional": budget_component,
    }


def _markdown_summary_count(path: Path, label: str) -> int:
    if not path.is_file():
        return 0
    match = re.search(
        rf"^-\s+{re.escape(label)}:\s+(\d+)\s*$",
        path.read_text(encoding="utf-8"),
        re.M,
    )
    return int(match.group(1)) if match else 0


def _budget_blocked_component(report: Any | None) -> str | None:
    if report is None:
        return None
    for record in getattr(report, "call_accounting", []) or []:
        if _value(getattr(record, "status", None)) == "Blocked":
            return str(getattr(record, "step_name", "llm-budget"))
    selected = getattr(report, "selected_backends", {}) or {}
    if selected.get("runtime_budget_blocked") == "true":
        return "runtime_llm_budget"
    return None


def _base_stage_blocking_issues(
    state: _ControllerState,
    base_blocking: list[str],
    base_safe: bool,
    base_status: str,
) -> list[str]:
    if base_safe:
        return []
    issues = list(base_blocking)
    if state.root_base_generation_failure_reason:
        issues.insert(0, state.root_base_generation_failure_reason)
    return list(dict.fromkeys(issue for issue in issues if issue)) or [base_status]


def _slug_reason(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized[:120] or "base_generation_failed"


def _artifact_paths(*artifacts: ArtifactRef | None) -> list[str]:
    return [artifact.path for artifact in artifacts if artifact is not None]


def _prepare_resume(
    *,
    run_id: str,
    root: Path,
) -> tuple[AutonomousPaperCheckpointVerification, list[str], str]:
    verification = verify_autonomous_paper_checkpoints(run_id=run_id, root=root)
    if verification.blockers:
        raise AutonomousPaperRunError(
            "Resume blocked by checkpoint verification: " + "; ".join(verification.blockers)
        )
    latest_by_stage = {
        checkpoint.stage_name: checkpoint for checkpoint in verification.checkpoints
    }
    reusable: list[str] = []
    stages = [
        "base_generation",
        "autonomous_loop",
        "final_manuscript_regeneration",
        "final_release_bundle_assembly",
    ]
    actual = "final_bundle_verification"
    for index, stage_name in enumerate(stages):
        checkpoint = latest_by_stage.get(stage_name)
        if checkpoint is None:
            later = [name for name in stages[index + 1 :] if name in latest_by_stage]
            if later:
                raise AutonomousPaperRunError(
                    f"Resume checkpoint sequence is inconsistent: {stage_name} is missing "
                    f"before {', '.join(later)}."
                )
            if _stage_output_exists(root, run_id, stage_name):
                raise AutonomousPaperRunError(
                    f"Resume blocked: immutable {stage_name} outputs exist without a checkpoint."
                )
            actual = stage_name
            break
        semantic_errors = _resume_semantic_errors(
            stage_name=stage_name,
            run_id=run_id,
            root=root,
        )
        if semantic_errors:
            raise AutonomousPaperRunError(
                f"Resume blocked by {stage_name} safety verification: "
                + "; ".join(semantic_errors)
            )
        reusable.append(stage_name)
    return verification, reusable, actual


def _resume_semantic_errors(
    *,
    stage_name: str,
    run_id: str,
    root: Path,
) -> list[str]:
    errors: list[str] = []
    if stage_name == "base_generation":
        summary = inspect_paper_bundle_summary(run_id=run_id, root=root)
        if summary.get("release_status") not in {
            "ReadyForHumanReview",
            "ReadyForHumanReviewWithWarnings",
        }:
            errors.append("base release status is not reusable")
    elif stage_name == "autonomous_loop":
        report, _ = latest_autonomous_loop_report(root, run_id)
        if report is None:
            errors.append("autonomous loop report is missing or unreadable")
        elif (
            report.requires_human_intervention
            or report.final_publication_ready
            or report.blocking_gap_count
            or int(report.final_claim_evidence_counts.get("unsupported", 0))
        ):
            errors.append("autonomous loop terminal state is not safe for reuse")
    elif stage_name == "final_manuscript_regeneration":
        report, _ = latest_final_manuscript_regeneration(root, run_id)
        if report is None or not _final_manuscript_is_safe(report):
            errors.append("final manuscript regeneration report is missing or unsafe")
    elif stage_name == "final_release_bundle_assembly":
        report, _ = latest_final_release_bundle(root, run_id)
        if report is None or not _final_bundle_is_complete(report):
            errors.append("final release bundle report is missing or incomplete")
        else:
            verification = verify_final_release_bundle(
                bundle_path=root / report.bundle_path,
                root=root,
            )
            if not _final_verification_is_safe(verification):
                errors.append("final release bundle hash or safety verification failed")
    if stage_name in {"autonomous_loop", "final_manuscript_regeneration"}:
        claim_map_path = latest_claim_evidence_map_path(root, run_id)
        if claim_map_path is None:
            errors.append("claim-evidence map is missing")
        else:
            try:
                claim_map = ClaimEvidenceMap.model_validate_json(
                    claim_map_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                errors.append("claim-evidence map is corrupt")
            else:
                unsupported_count = int(
                    claim_map.summary_counts.get("unsupported", 0)
                ) or len(claim_map.unsupported_non_scaffold_claim_ids)
                if claim_map.publication_ready or unsupported_count:
                    errors.append("claim-evidence map contains unsafe authority or claims")
    if stage_name in {"base_generation", "autonomous_loop", "final_manuscript_regeneration"}:
        lint = lint_paper_bundle_summary(run_id=run_id, root=root)
        unsupported = int(lint.get("claim_evidence_unsupported_count") or 0)
        if _lint_has_safety_block(lint, unsupported):
            errors.append("claim or citation safety lint blocks checkpoint reuse")
    return sorted(set(errors))


def _stage_output_exists(root: Path, run_id: str, stage_name: str) -> bool:
    if stage_name == "base_generation":
        return (root / "runs" / run_id / "reports" / "llm-orchestration-report.json").is_file()
    if stage_name == "autonomous_loop":
        report, _ = latest_autonomous_loop_report(root, run_id)
        return report is not None
    if stage_name == "final_manuscript_regeneration":
        report, _ = latest_final_manuscript_regeneration(root, run_id)
        return report is not None
    if stage_name == "final_release_bundle_assembly":
        report, _ = latest_final_release_bundle(root, run_id)
        return report is not None
    return False


def _resume_checkpoint(
    state: _ControllerState,
    stage_name: str,
) -> Any:
    if state.resume_verification is None:
        raise AutonomousPaperRunError("Resume checkpoint state is unavailable.")
    for checkpoint in reversed(state.resume_verification.checkpoints):
        if checkpoint.stage_name == stage_name:
            return checkpoint
    raise AutonomousPaperRunError(f"Verified checkpoint not found for {stage_name}.")


def _reused_stage(checkpoint: Any, clock: Clock) -> AutonomousPaperRunStage:
    timestamp = clock.now()
    return _stage(
        checkpoint.stage_name,
        "reused",
        timestamp,
        timestamp,
        "Reused after checkpoint, artifact, safety, protocol, and ledger verification.",
        artifacts=list(checkpoint.stage_artifact_paths),
        warnings=(
            ["Reused checkpoint retained nonblocking stage warnings."]
            if checkpoint.verification_status == "verified_with_warnings"
            else []
        ),
    )


def _checkpoint_stage(
    state: _ControllerState,
    stage: AutonomousPaperRunStage,
    artifact_paths: list[str | None],
    *,
    safety_gate_status: str | None = None,
    release_status: str | None = None,
) -> None:
    if state.checkpoint_session is None:
        return
    state.checkpoint_session.record(
        stage,
        [path for path in artifact_paths if path],
        safety_gate_status=(
            safety_gate_status
            or (
                "passed_with_warnings"
                if stage.stage_status == "completed_with_warnings"
                else "passed"
            )
        ),
        release_status=release_status,
    )


def _base_external_usage(root: Path, run_id: str) -> tuple[bool, bool]:
    path = root / "runs" / run_id / "reports" / "llm-orchestration-report.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, False
    network_used = any(
        bool(item.get("external_call_performed"))
        for item in payload.get("call_accounting", [])
        if isinstance(item, dict)
    )
    return network_used, network_used


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _final_manuscript_is_safe(report: Any) -> bool:
    return bool(
        report.regeneration_status == "completed"
        and report.publication_ready is False
        and report.unsupported_claim_count == 0
        and report.claim_support_rechecked_after_regeneration
        and report.claim_evidence_map_rechecked_after_regeneration
        and report.citation_safety_rechecked_after_regeneration
        and report.quality_lint_rechecked_after_regeneration
        and report.release_rechecked_after_regeneration
    )


def _final_bundle_is_complete(report: Any) -> bool:
    return bool(report.bundle_status == "complete" and not report.missing_required_artifacts)


def _final_verification_is_safe(report: Any) -> bool:
    return bool(
        report.verification_status in {"verified", "verified_with_warnings"}
        and report.checks_failed == 0
        and report.hash_mismatch_count == 0
        and report.missing_required_artifact_count == 0
        and report.rejected_reference_leak_count == 0
        and report.claim_evidence_check_passed
        and report.release_report_check_passed
        and report.publication_ready is False
    )


def _lint_has_safety_block(lint: dict[str, Any], unsupported_claim_count: int) -> bool:
    return bool(
        int(lint.get("claim_support_missing_required_citation_count") or 0)
        or int(lint.get("citation_as_validation_misuse_count") or 0)
        or unsupported_claim_count
        or int(lint.get("claim_support_forbidden_claim_count") or 0)
        or not bool(lint.get("citation_registry_sources_all_accepted", True))
        or bool(lint.get("publication_ready"))
    )


__all__ = [
    "AutonomousPaperInjectedCrash",
    "AutonomousPaperRunError",
    "AutonomousPaperRunResult",
    "autonomous_paper_run_summary_fields",
    "inspect_autonomous_paper_run",
    "latest_autonomous_paper_run",
    "render_autonomous_paper_run_markdown",
    "run_autonomous_paper",
]
