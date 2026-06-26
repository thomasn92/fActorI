"""Safety checks for gated synthetic experiment adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from factori.evidence import is_synthetic_experiment_evidence
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    DataRequirement,
    ExperimentRunContract,
    ExperimentRunResult,
    VerificationLabel,
)

MAX_EXPERIMENT_TIMEOUT_SECONDS = 60
MAX_EXPERIMENT_REPLICATIONS = 100
ALLOWED_MVP_DATA_REGIMES = frozenset(
    {DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY}
)
NETWORK_MARKERS = ("http://", "https://", "ftp://", "curl ", "wget ")
NON_EXPERIMENT_EVIDENCE_ROLES = frozenset(
    {
        "llm_prompt",
        "llm_response",
        "llm_parse_report",
        "llm_reviewer",
        "retrieval_evidence",
        "literature_evidence",
        "fake_proof",
        "proof",
        "real_data_experiment",
    }
)


@dataclass(frozen=True)
class ExperimentContractValidationResult:
    """Deterministic validation result for a synthetic experiment contract."""

    valid: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentResultValidationResult:
    """Deterministic validation result for a synthetic experiment result."""

    valid: bool
    reasons: tuple[str, ...]


def validate_experiment_contract(
    contract: ExperimentRunContract,
) -> ExperimentContractValidationResult:
    """Validate a synthetic experiment request before any runner execution."""
    reasons: list[str] = []
    if not contract.candidate_id.strip():
        reasons.append("candidate_id is required")
    if not contract.claim_id.strip():
        reasons.append("claim_id is required")
    if not contract.experiment_id.strip():
        reasons.append("experiment_id is required")
    if contract.data_regime not in ALLOWED_MVP_DATA_REGIMES:
        reasons.append("MVP experiments allow only NoData or SyntheticOnly data regimes")
    if (
        contract.data_regime == DataRequirement.SYNTHETIC_ONLY
        and not contract.synthetic_data_spec
    ):
        reasons.append("SyntheticOnly experiments require a synthetic_data_spec")
    if not contract.metrics:
        reasons.append("experiment metrics are required")
    if not contract.acceptance_criteria:
        reasons.append("acceptance_criteria are required")
    if contract.random_seed is None:
        reasons.append("random_seed is required")
    if contract.replications < 1 or contract.replications > MAX_EXPERIMENT_REPLICATIONS:
        reasons.append("experiment replications must be between 1 and 100")
    if contract.timeout_seconds < 1 or contract.timeout_seconds > MAX_EXPERIMENT_TIMEOUT_SECONDS:
        reasons.append("experiment timeout must be between 1 and 60 seconds")
    payload = _flatten_text(
        {
            "synthetic_data_spec": contract.synthetic_data_spec,
            "model_spec": contract.model_spec,
            "algorithm_spec": contract.algorithm_spec,
            "forbidden_external_inputs": contract.forbidden_external_inputs,
        }
    )
    if any(marker in payload for marker in NETWORK_MARKERS):
        reasons.append("experiment contract must not depend on network access")
    if _contains_absolute_external_input(contract):
        reasons.append("absolute external inputs are not allowed")
    if any(
        forbidden in {"PublicDownload", "UserProvided", "RealWorldData"}
        for forbidden in contract.forbidden_external_inputs
    ) is False:
        reasons.append("forbidden_external_inputs must include real/public/user data regimes")
    return ExperimentContractValidationResult(valid=not reasons, reasons=tuple(reasons))


def validate_experiment_result(
    result: ExperimentRunResult,
    contract: ExperimentRunContract,
    evidence_artifacts: Iterable[ArtifactRef] = (),
) -> ExperimentResultValidationResult:
    """Validate local runner output before it can support a synthetic label."""
    reasons: list[str] = []
    contract_validation = validate_experiment_contract(contract)
    if not contract_validation.valid:
        reasons.extend(f"contract: {reason}" for reason in contract_validation.reasons)
    if result.candidate_id != contract.candidate_id:
        reasons.append("result candidate_id does not match contract")
    if result.claim_id != contract.claim_id:
        reasons.append("result claim_id does not match contract")
    if result.experiment_id != contract.experiment_id:
        reasons.append("result experiment_id does not match contract")
    if result.data_regime not in ALLOWED_MVP_DATA_REGIMES:
        reasons.append("result data regime must remain NoData or SyntheticOnly")
    if result.passed and result.exit_code != 0:
        reasons.append("passed=true requires exit_code == 0")
    if not result.stdout_hash:
        reasons.append("stdout hash is required")
    if not result.stderr_hash:
        reasons.append("stderr hash is required")
    if not result.input_spec_hash:
        reasons.append("input spec hash is required")
    if not result.output_payload_hash:
        reasons.append("output payload hash is required")
    if result.backend == "fake" and result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        reasons.append("fake backend cannot masquerade as real experiment evidence")
    if result.label == VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED:
        reasons.append("synthetic experiments cannot produce RealDataExperimentVerified")
    if result.label == VerificationLabel.EXPERIMENT_VERIFIED:
        reasons.append("generic ExperimentVerified is not allowed for MVP synthetic evidence")
    if result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        if result.backend == "fake":
            reasons.append("SyntheticExperimentVerified requires a real/local experiment backend")
        if result.data_regime != DataRequirement.SYNTHETIC_ONLY:
            reasons.append("SyntheticExperimentVerified requires SyntheticOnly data")
        if not contract.allow_external_tools:
            reasons.append("SyntheticExperimentVerified requires allow_external_tools=true")
        if result.exit_code != 0:
            reasons.append("SyntheticExperimentVerified requires exit_code == 0")
        if not result.passed:
            reasons.append("SyntheticExperimentVerified requires passed=true")
        if not result.raw_trace_artifact_id:
            reasons.append("SyntheticExperimentVerified requires a raw experiment trace artifact")
        if not result.safety_report_artifact_id:
            reasons.append("SyntheticExperimentVerified requires a safety validation artifact")
        if not metrics_satisfy_acceptance(result.metrics, contract.acceptance_criteria):
            reasons.append("metrics do not satisfy acceptance_criteria")
    reasons.extend(_experiment_artifact_reasons(list(evidence_artifacts)))
    return ExperimentResultValidationResult(valid=not reasons, reasons=tuple(reasons))


def experiment_label_allowed_by_result(
    result: ExperimentRunResult,
    contract: ExperimentRunContract,
    evidence_artifacts: Iterable[ArtifactRef],
) -> bool:
    """Return whether result plus linked artifacts justify SyntheticExperimentVerified."""
    validation = validate_experiment_result(result, contract, evidence_artifacts)
    return (
        validation.valid
        and result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        and any(is_synthetic_experiment_evidence(artifact) for artifact in evidence_artifacts)
    )


def metrics_satisfy_acceptance(
    metrics: Mapping[str, float],
    acceptance_criteria: Mapping[str, Any],
) -> bool:
    """Evaluate simple deterministic min/max acceptance criteria."""
    if not metrics or not acceptance_criteria:
        return False
    for metric_name, rule in acceptance_criteria.items():
        if metric_name not in metrics:
            return False
        value = float(metrics[metric_name])
        if isinstance(rule, Mapping):
            if "min" in rule and value < float(rule["min"]):
                return False
            if "max" in rule and value > float(rule["max"]):
                return False
        elif value < float(rule):
            return False
    return True


def _experiment_artifact_reasons(artifacts: list[ArtifactRef]) -> list[str]:
    reasons: list[str] = []
    for artifact in artifacts:
        suffix = artifact.path.rsplit(".", maxsplit=1)[-1].lower() if "." in artifact.path else ""
        evidence_role = str(artifact.metadata.get("evidence_role", ""))
        if artifact.type in {ArtifactType.LATEX} or suffix in {"md", "markdown", "tex", "pdf"}:
            reasons.append("presentation artifacts cannot justify experiment labels")
        if evidence_role in NON_EXPERIMENT_EVIDENCE_ROLES:
            reasons.append(f"{evidence_role} artifacts cannot justify experiment labels")
    return reasons


def _contains_absolute_external_input(contract: ExperimentRunContract) -> bool:
    text = _flatten_text(
        {
            "synthetic_data_spec": contract.synthetic_data_spec,
            "model_spec": contract.model_spec,
            "algorithm_spec": contract.algorithm_spec,
        }
    )
    return "file:///" in text or " /" in f" {text}" or "\\\\" in text


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_flatten_text(item)}" for key, item in value.items()).lower()
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value).lower()
    return str(value).lower()


__all__ = [
    "ALLOWED_MVP_DATA_REGIMES",
    "ExperimentContractValidationResult",
    "ExperimentResultValidationResult",
    "MAX_EXPERIMENT_REPLICATIONS",
    "MAX_EXPERIMENT_TIMEOUT_SECONDS",
    "experiment_label_allowed_by_result",
    "metrics_satisfy_acceptance",
    "validate_experiment_contract",
    "validate_experiment_result",
]
