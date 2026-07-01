"""Gated uv-based execution of approved local Python experiment bundles."""

from __future__ import annotations

import ast
import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import persist_autonomous_evidence_gap_plan
from factori.claim_evidence import (
    build_claim_evidence_map,
    latest_claim_evidence_map_path,
    persist_claim_evidence_map,
)
from factori.hashing import canonical_json, sha256_file, sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ClaimEvidenceMap,
    ControllerActionType,
    ExperimentArtifact,
    FullPaperReleaseGateConfig,
    PlannedExperimentSpec,
    PythonExperimentSandboxIndex,
    PythonExperimentSandboxManifest,
    PythonExperimentSandboxReport,
)
from factori.storage_protocols import Clock, SystemClock

_BACKENDS = {"uv_local", "fake"}
_MODES = {"dry_run", "apply"}
_ALLOWED_DEPENDENCIES = {
    "numpy",
    "pandas",
    "scipy",
    "scikit-learn",
    "matplotlib",
}
_ALLOWED_IMPORTS = {
    "csv",
    "hashlib",
    "json",
    "math",
    "pathlib",
    "random",
    "statistics",
} | {name.replace("-", "_") for name in _ALLOWED_DEPENDENCIES}
_BLOCKED_IMPORTS = {
    "asyncio",
    "ctypes",
    "ftplib",
    "http",
    "httpx",
    "multiprocessing",
    "requests",
    "shlex",
    "socket",
    "subprocess",
    "telnetlib",
    "urllib",
}
_BLOCKED_CALLS = {"compile", "eval", "exec", "__import__"}
_AUTHORITY_MARKERS = (
    "publication ready",
    "publication-ready",
    "ready for publication",
    "scientifically validated",
    "broad empirical validation",
    "novelty confirmed",
    "novelty validated",
    "correctness validated",
)
_EXPERIMENT_CLAIM_CLASSES = {
    "demonstration_claim",
    "experiment_claim",
    "external_factual_claim",
    "pipeline_status_claim",
    "result_claim",
}
_RESOURCE_LIMITS = {
    "cpu_seconds": 30,
    "memory_bytes": 512 * 1024 * 1024,
    "output_bytes": 32 * 1024 * 1024,
}


class PythonExperimentSandboxError(RuntimeError):
    """Raised when a local experiment cannot be executed within sandbox policy."""


@dataclass(frozen=True)
class PythonExperimentSandboxResult:
    """One persisted sandbox execution report and derived latest index."""

    run_id: str
    report: PythonExperimentSandboxReport
    index: PythonExperimentSandboxIndex
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef


def run_python_experiment_sandbox(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    experiment_spec: str | Path | PlannedExperimentSpec,
    sandbox_backend: str = "uv_local",
    execution_mode: str = "dry_run",
    clock: Clock | None = None,
) -> PythonExperimentSandboxResult:
    """Validate and optionally execute one approved local experiment bundle."""
    if sandbox_backend not in _BACKENDS:
        raise PythonExperimentSandboxError("sandbox backend must be uv_local or fake")
    execution_mode = execution_mode.replace("-", "_")
    if execution_mode not in _MODES:
        raise PythonExperimentSandboxError("execution mode must be dry_run or apply")
    root_path = Path(root).resolve()
    run_path = root_path / "runs" / run_id
    if not run_path.is_dir():
        raise PythonExperimentSandboxError(f"No run directory found for run_id={run_id}.")
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise PythonExperimentSandboxError("Ledger validation blocks sandbox execution.")

    spec = _load_spec(experiment_spec)
    if spec.run_id != run_id:
        raise PythonExperimentSandboxError("experiment spec run_id does not match requested run")
    bundle = _resolve_bundle(root_path, spec)
    policy = _validate_bundle_policy(root_path, run_id, spec, bundle)
    _validate_target_claim(root_path, run_id, spec)

    number = _next_run_number(run_path / "reports")
    sandbox_run_id = f"python-experiment-sandbox-run-{number:04d}"
    workdir = run_path / "experiments" / sandbox_run_id
    expected = _expected_paths(root_path, run_id, sandbox_run_id)
    hashes = _bundle_hashes(bundle)
    command = ["uv", "run", "--offline", "--frozen", "--no-dev", "python", "experiment.py"]
    base = {
        "run_id": run_id,
        "experiment_spec_id": spec.spec_id,
        "sandbox_run_id": sandbox_run_id,
        "sandbox_backend": sandbox_backend,
        "execution_mode": execution_mode,
        "experiment_bundle_path": _display_path(bundle, root_path),
        "working_directory": _display_path(workdir, root_path),
        "pyproject_path": _display_path(workdir / "pyproject.toml", root_path),
        "uv_lock_path_optional": _display_path(workdir / "uv.lock", root_path),
        "dependency_policy": policy,
        "network_disabled": True,
        "seed": spec.seed,
        "timeout_seconds": spec.timeout_seconds,
        "resource_limits": _RESOURCE_LIMITS,
        "command_executed": command,
        "stdout_path": expected["stdout"],
        "stderr_path": expected["stderr"],
        "metrics_path": expected["metrics"],
        "artifact_manifest_path": expected["manifest"],
        "config_hash": hashes["config_hash"],
        "code_hash": hashes["code_hash"],
        "input_hash": hashes["input_hash"],
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    if execution_mode == "dry_run":
        report = PythonExperimentSandboxReport(
            **base,
            sandbox_status="dry_run_ready",
            claim_evidence_map_rebuilt=False,
            release_rechecked=False,
        )
        return _persist_report(report, root_path, store, ledger, number)

    if sandbox_backend == "fake":
        raise PythonExperimentSandboxError(
            "fake sandbox is schema-gated but cannot create experiment evidence"
        )
    workdir.mkdir(parents=True, exist_ok=False)
    _copy_bundle(bundle, workdir)
    stdout = ""
    stderr = ""
    status = "failed"
    metrics: dict[str, Any] | None = None
    metrics_hash: str | None = None
    output_hash: str | None = None
    created_candidate: str | None = None
    ingested_path: str | None = None
    map_rebuilt = False
    release_rechecked = False
    try:
        lock_stdout, lock_stderr = _ensure_lock(workdir, spec.timeout_seconds)
        process = _execute_uv(workdir, command, spec.seed, spec.timeout_seconds)
        stdout = lock_stdout + process.stdout
        stderr = lock_stderr + process.stderr
        if process.returncode != 0:
            status = "failed"
        else:
            metrics = _load_metrics(workdir / "metrics.json")
            _validate_output_authority(metrics, workdir / "outputs")
            metrics_hash = sha256_file(workdir / "metrics.json")
            output_hash = _hash_outputs(workdir / "outputs")
            status = "completed"
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_stream(exc.stdout)
        stderr = _decode_timeout_stream(exc.stderr)
        status = "timed_out"
    except PythonExperimentSandboxError as exc:
        stderr = str(exc)
        status = "rejected_policy_violation"

    _write_text_atomic(workdir / "stdout.txt", stdout)
    _write_text_atomic(workdir / "stderr.txt", stderr)
    manifest = _build_manifest(run_id, sandbox_run_id, workdir, policy)
    _write_json_atomic(workdir / "artifact-manifest.json", manifest.model_dump(mode="json"))

    if status == "completed" and metrics is not None:
        from factori.evidence_artifact_intake import (  # noqa: PLC0415
            EvidenceArtifactIntakeError,
            ingest_experiment_artifact,
        )

        candidate = _build_experiment_artifact(
            root_path=root_path,
            run_id=run_id,
            spec=spec,
            sandbox_run_id=sandbox_run_id,
            workdir=workdir,
            metrics=metrics,
            config_hash=hashes["config_hash"],
            code_hash=hashes["code_hash"],
            clock=clock or SystemClock(),
        )
        candidate_path = workdir / "experiment-artifact-candidate.json"
        _write_json_atomic(candidate_path, candidate.model_dump(mode="json"))
        created_candidate = _display_path(candidate_path, root_path)
        try:
            ingested = ingest_experiment_artifact(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                experiment_file=candidate_path,
            )
            ingested_path = ingested.experiment_artifact.path
            persist_claim_evidence_map(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
            )
            map_rebuilt = True
            persist_autonomous_evidence_gap_plan(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                backend="deterministic",
            )
            from factori.full_paper_release import evaluate_full_paper_release  # noqa: PLC0415

            evaluate_full_paper_release(
                run_id=run_id,
                root=root_path,
                ledger=ledger,
                config=FullPaperReleaseGateConfig(run_id=run_id, write_report=False),
            )
            release_rechecked = True
        except EvidenceArtifactIntakeError as exc:
            status = "rejected_policy_violation"
            _write_text_atomic(workdir / "stderr.txt", f"{stderr}\n{exc}".strip())
            ingested_path = None

    report_payload = {
        **base,
        "sandbox_status": status,
        "uv_lock_path_optional": (
            _display_path(workdir / "uv.lock", root_path)
            if (workdir / "uv.lock").is_file()
            else None
        ),
        "metrics_hash_optional": metrics_hash,
        "output_hash_optional": output_hash,
        "created_experiment_artifact_path_optional": created_candidate,
        "ingested_experiment_artifact_path_optional": ingested_path,
        "claim_evidence_map_rebuilt": map_rebuilt,
        "release_rechecked": release_rechecked,
    }
    report = PythonExperimentSandboxReport.model_validate(report_payload)
    return _persist_report(report, root_path, store, ledger, number)


def inspect_python_experiment_sandbox(
    *, run_id: str, root: str | Path = "."
) -> dict[str, Any]:
    """Read the latest Python sandbox report and index."""
    root_path = Path(root)
    report, index = latest_python_experiment_sandbox_report(root_path, run_id)
    if report is None or index is None:
        raise PythonExperimentSandboxError(
            f"No Python experiment sandbox report found for run_id={run_id}."
        )
    return {
        **report.model_dump(mode="json"),
        **python_experiment_sandbox_summary_fields(report, index),
        "python_experiment_sandbox_index": index.model_dump(mode="json"),
    }


def latest_python_experiment_sandbox_report(
    root: Path, run_id: str
) -> tuple[PythonExperimentSandboxReport | None, PythonExperimentSandboxIndex | None]:
    """Return the latest immutable sandbox report and index."""
    reports = root / "runs" / run_id / "reports"
    indexes = sorted(
        path
        for path in reports.glob("python-experiment-sandbox-index-*.json")
        if not path.name.endswith(".meta.json")
    )
    if not indexes:
        return None, None
    try:
        index = PythonExperimentSandboxIndex.model_validate_json(
            indexes[-1].read_text(encoding="utf-8")
        )
        path = root / index.latest_report_path
        report = PythonExperimentSandboxReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None, None
    return report, index


def python_experiment_sandbox_summary_fields(
    report: PythonExperimentSandboxReport | None,
    index: PythonExperimentSandboxIndex | None,
) -> dict[str, Any]:
    """Return stable inspect, lint, and reviewer summary fields."""
    if report is None or index is None:
        return {
            "python_experiment_sandbox_present": False,
            "python_experiment_sandbox_run_count": 0,
            "latest_python_sandbox_status": None,
            "python_experiment_sandbox_completed_count": 0,
            "python_experiment_sandbox_failed_count": 0,
            "python_experiment_artifacts_created_count": 0,
            "python_experiment_sandbox_network_disabled": True,
        }
    return {
        "python_experiment_sandbox_present": True,
        "python_experiment_sandbox_run_count": index.sandbox_run_count,
        "latest_python_sandbox_status": index.latest_sandbox_status,
        "python_experiment_sandbox_completed_count": index.completed_count,
        "python_experiment_sandbox_failed_count": index.failed_count,
        "python_experiment_artifacts_created_count": index.experiment_artifacts_created_count,
        "python_experiment_sandbox_network_disabled": index.network_disabled,
    }


def render_python_experiment_sandbox_markdown(
    report: PythonExperimentSandboxReport,
) -> str:
    """Render a concise non-evidence sandbox report."""
    return "\n".join(
        [
            "# Python Experiment Sandbox",
            "",
            f"- Sandbox run: `{report.sandbox_run_id}`",
            f"- Mode/backend: `{report.execution_mode}` / `{report.sandbox_backend}`",
            f"- Status: `{report.sandbox_status}`",
            f"- Network disabled: `{str(report.network_disabled).lower()}`",
            "- Experiment artifact ingested: "
            f"`{bool(report.ingested_experiment_artifact_path_optional)}`",
            "- Publication ready: `false`",
            "",
            "This sandbox report is execution context, not scientific validation "
            "or publication authority.",
            "",
        ]
    )


def _load_spec(path_or_spec: str | Path | PlannedExperimentSpec) -> PlannedExperimentSpec:
    if isinstance(path_or_spec, PlannedExperimentSpec):
        return path_or_spec
    try:
        return PlannedExperimentSpec.model_validate_json(
            Path(path_or_spec).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise PythonExperimentSandboxError(f"Invalid experiment spec: {exc}") from exc


def _resolve_bundle(root: Path, spec: PlannedExperimentSpec) -> Path:
    if not spec.experiment_bundle_path_optional:
        raise PythonExperimentSandboxError("experiment spec does not select an approved bundle")
    requested = Path(spec.experiment_bundle_path_optional)
    bundle = (requested if requested.is_absolute() else root / requested).resolve()
    if not bundle.is_dir():
        raise PythonExperimentSandboxError(f"experiment bundle does not exist: {bundle}")
    return bundle


def _validate_bundle_policy(
    root: Path, run_id: str, spec: PlannedExperimentSpec, bundle: Path
) -> dict[str, Any]:
    approved_roots = [
        (root / "tests" / "fixtures" / "experiments" / "bundles").resolve(),
        (root / "runs" / run_id / "approved-experiment-bundles").resolve(),
    ]
    if not any(_is_relative_to(bundle, candidate) for candidate in approved_roots):
        raise PythonExperimentSandboxError("bundle path is outside approved local bundle roots")
    required = [
        bundle / "experiment.py",
        bundle / "experiment_config.json",
        bundle / "pyproject.toml",
    ]
    if any(not path.is_file() for path in required):
        raise PythonExperimentSandboxError(
            "bundle must contain experiment.py, experiment_config.json, and pyproject.toml"
        )
    if spec.allow_network:
        raise PythonExperimentSandboxError("experiment spec requests network access")
    if spec.shell_command_optional:
        raise PythonExperimentSandboxError("experiment spec contains a shell command")
    config = json.loads((bundle / "experiment_config.json").read_text(encoding="utf-8"))
    if config.get("approved_local_bundle") is not True:
        raise PythonExperimentSandboxError("bundle lacks approved_local_bundle=true")
    dependencies = _pyproject_dependencies(bundle / "pyproject.toml")
    requested = {_normalize_dependency(item) for item in spec.requested_dependencies}
    all_dependencies = sorted(dependencies | requested)
    blocked = sorted(set(all_dependencies) - _ALLOWED_DEPENDENCIES)
    if blocked:
        raise PythonExperimentSandboxError(
            f"dependencies are outside the allowlist: {', '.join(blocked)}"
        )
    _validate_python_ast(bundle / "experiment.py", dependencies)
    return {
        "allowed_dependencies": all_dependencies,
        "blocked_dependencies": [],
        "allow_network": False,
        "allow_subprocess": False,
        "allow_file_write_outside_workspace": False,
    }


def _validate_target_claim(root: Path, run_id: str, spec: PlannedExperimentSpec) -> None:
    map_path = latest_claim_evidence_map_path(root, run_id)
    if map_path is None:
        try:
            claim_map = build_claim_evidence_map(run_id=run_id, root=root)
        except Exception as exc:
            raise PythonExperimentSandboxError(
                "claim-evidence map could not be built for target validation"
            ) from exc
    else:
        try:
            claim_map = ClaimEvidenceMap.model_validate_json(map_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise PythonExperimentSandboxError("claim-evidence map is corrupt") from exc
    links = [link for link in claim_map.links if link.claim_id == spec.target_claim_id]
    if not links:
        raise PythonExperimentSandboxError("target claim is absent from the claim-evidence map")
    if links[0].claim_class not in _EXPERIMENT_CLAIM_CLASSES:
        raise PythonExperimentSandboxError("target claim is outside experiment support policy")


def _pyproject_dependencies(path: Path) -> set[str]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PythonExperimentSandboxError("invalid pyproject.toml") from exc
    values = payload.get("project", {}).get("dependencies", [])
    if not isinstance(values, list):
        raise PythonExperimentSandboxError("pyproject dependencies must be a list")
    return {_normalize_dependency(str(value)) for value in values}


def _normalize_dependency(value: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", value.strip())
    if not match:
        raise PythonExperimentSandboxError(f"invalid dependency declaration: {value}")
    return match.group(0).casefold().replace("_", "-")


def _validate_python_ast(path: Path, dependencies: set[str]) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    except (OSError, SyntaxError) as exc:
        raise PythonExperimentSandboxError("experiment.py is unreadable or invalid") from exc
    allowed = _ALLOWED_IMPORTS | {item.replace("-", "_") for item in dependencies}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".", 1)[0]]
        else:
            names = []
        for name in names:
            if name in _BLOCKED_IMPORTS or name not in allowed:
                raise PythonExperimentSandboxError(f"experiment import is not allowed: {name}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
                raise PythonExperimentSandboxError(
                    f"experiment call is not allowed: {node.func.id}"
                )
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "system",
                "popen",
                "spawn",
                "fork",
            }:
                raise PythonExperimentSandboxError(
                    f"experiment process call is not allowed: {node.func.attr}"
                )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            candidate = node.value
            if candidate.startswith(("/", "~")) or ".." in Path(candidate).parts:
                raise PythonExperimentSandboxError("experiment contains an escaping path literal")


def _bundle_hashes(bundle: Path) -> dict[str, str]:
    return {
        "config_hash": sha256_file(bundle / "experiment_config.json"),
        "code_hash": sha256_file(bundle / "experiment.py"),
        "input_hash": sha256_json(
            {
                path.relative_to(bundle).as_posix(): sha256_file(path)
                for path in sorted(bundle.rglob("*"))
                if path.is_file() and path.name not in {"uv.lock", "README.md"}
            }
        ),
    }


def _copy_bundle(bundle: Path, workdir: Path) -> None:
    for source in bundle.iterdir():
        if source.name in {"metrics.json", "outputs", ".venv", "__pycache__"}:
            continue
        destination = workdir / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def _ensure_lock(workdir: Path, timeout: int) -> tuple[str, str]:
    if (workdir / "uv.lock").is_file():
        return "", ""
    process = subprocess.run(
        ["uv", "lock", "--offline"],
        cwd=workdir,
        env=_sandbox_env(workdir, 0),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )
    if process.returncode != 0 or not (workdir / "uv.lock").is_file():
        raise PythonExperimentSandboxError(
            f"uv lock could not be generated offline: {process.stderr.strip()}"
        )
    return process.stdout, process.stderr


def _execute_uv(
    workdir: Path, command: list[str], seed: int, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=workdir,
        env=_sandbox_env(workdir, seed),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
        preexec_fn=_resource_limiter(timeout),
    )


def _sandbox_env(workdir: Path, seed: int) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workdir / ".home"),
        "PYTHONHASHSEED": str(seed),
        "FACTORI_EXPERIMENT_SEED": str(seed),
        "UV_OFFLINE": "1",
        "UV_CACHE_DIR": str(workdir / ".uv-cache"),
        "NO_PROXY": "*",
        "no_proxy": "*",
    }
    return env


def _resource_limiter(timeout: int):
    if os.name != "posix":
        return None

    def apply_limits() -> None:
        import resource

        cpu = min(timeout, _RESOURCE_LIMITS["cpu_seconds"])
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (_RESOURCE_LIMITS["output_bytes"], _RESOURCE_LIMITS["output_bytes"]),
        )
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(
                resource.RLIMIT_AS,
                (_RESOURCE_LIMITS["memory_bytes"], _RESOURCE_LIMITS["memory_bytes"]),
            )

    return apply_limits


def _load_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PythonExperimentSandboxError("completed process did not write metrics.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PythonExperimentSandboxError("metrics.json is invalid") from exc
    if not isinstance(value, dict) or not value:
        raise PythonExperimentSandboxError("metrics.json must be a non-empty object")
    return value


def _validate_output_authority(metrics: dict[str, Any], outputs: Path) -> None:
    text = canonical_json(metrics).casefold()
    if outputs.is_dir():
        for path in outputs.rglob("*"):
            if path.is_file() and path.stat().st_size <= 1_000_000:
                try:
                    text += "\n" + path.read_text(encoding="utf-8").casefold()
                except UnicodeDecodeError:
                    continue
    if any(marker in text for marker in _AUTHORITY_MARKERS):
        raise PythonExperimentSandboxError("experiment output claims forbidden authority")


def _hash_outputs(outputs: Path) -> str:
    if not outputs.is_dir():
        return sha256_json({})
    return sha256_json(
        {
            path.relative_to(outputs).as_posix(): sha256_file(path)
            for path in sorted(outputs.rglob("*"))
            if path.is_file()
        }
    )


def _build_manifest(
    run_id: str,
    sandbox_run_id: str,
    workdir: Path,
    policy: dict[str, Any],
) -> PythonExperimentSandboxManifest:
    files = {
        path.relative_to(workdir).as_posix(): sha256_file(path)
        for path in sorted(workdir.rglob("*"))
        if path.is_file() and ".venv" not in path.parts and ".uv-cache" not in path.parts
    }
    return PythonExperimentSandboxManifest(
        run_id=run_id,
        sandbox_run_id=sandbox_run_id,
        files=files,
        allowed_dependencies=list(policy["allowed_dependencies"]),
        blocked_dependencies=list(policy["blocked_dependencies"]),
        python_version=platform.python_version(),
        platform=platform.platform(),
        uv_version=_uv_version(),
        allow_network=False,
        allow_subprocess=False,
        allow_file_write_outside_workspace=False,
    )


def _uv_version() -> str:
    try:
        process = subprocess.run(
            ["uv", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return process.stdout.strip() or "unavailable"


def _build_experiment_artifact(
    *,
    root_path: Path,
    run_id: str,
    spec: PlannedExperimentSpec,
    sandbox_run_id: str,
    workdir: Path,
    metrics: dict[str, Any],
    config_hash: str,
    code_hash: str,
    clock: Clock,
) -> ExperimentArtifact:
    artifact_paths = [
        _display_path(workdir / name, root_path)
        for name in ("metrics.json", "stdout.txt", "stderr.txt", "artifact-manifest.json")
    ]
    now = clock.now()
    return ExperimentArtifact(
        run_id=run_id,
        experiment_id=f"uv-local-{sandbox_run_id}",
        experiment_type="synthetic_uv_local",
        claim_ids_or_section_ids=[spec.target_claim_id, _slug(spec.target_section)],
        hypothesis_or_question=spec.hypothesis_or_question,
        status="completed",
        dataset_name_optional=spec.suggested_dataset,
        dataset_hash_optional=sha256_json(
            {"bundle": spec.experiment_bundle_path_optional, "seed": spec.seed}
        ),
        config_hash=config_hash,
        code_commit_hash_optional=code_hash,
        command_optional="uv run --offline --frozen --no-dev python experiment.py",
        metrics=metrics,
        result_summary=(
            "This is a synthetic/local experiment artifact. It supports only the bounded "
            "mapped result claim for this run. It does not imply broad empirical validation, "
            "correctness validation, novelty, or publication readiness."
        ),
        artifact_paths=artifact_paths,
        limitations=[
            "Synthetic/local fixture experiment only.",
            "Network access was disabled by policy and uv ran in offline mode.",
            "Results are scoped to the declared claim, fixed configuration, and recorded seed.",
            "This artifact does not imply broad empirical validation or publication readiness.",
        ],
        created_at=now,
        ingested_at=now,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _persist_report(
    report: PythonExperimentSandboxReport,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    number: int,
) -> PythonExperimentSandboxResult:
    reports = root / "runs" / report.run_id / "reports"
    previous_reports = _load_reports(reports)
    all_reports = [*previous_reports, report]
    report_id = report.sandbox_run_id
    index_id = f"python-experiment-sandbox-index-{number:04d}"
    report_path = f"runs/{report.run_id}/reports/{report_id}.json"
    index = PythonExperimentSandboxIndex(
        run_id=report.run_id,
        latest_sandbox_run_id=report.sandbox_run_id,
        sandbox_run_count=len(all_reports),
        latest_sandbox_status=report.sandbox_status,
        completed_count=sum(item.sandbox_status == "completed" for item in all_reports),
        failed_count=sum(
            item.sandbox_status in {
                "failed",
                "timed_out",
                "rejected_policy_violation",
                "not_reproducible",
            }
            for item in all_reports
        ),
        experiment_artifacts_created_count=sum(
            bool(item.ingested_experiment_artifact_path_optional) for item in all_reports
        ),
        network_disabled=all(item.network_disabled for item in all_reports),
        latest_report_path=report_path,
    )
    metadata = {
        "stage": "python_experiment_sandbox",
        "artifact_role": "sandbox_execution_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
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
                render_python_experiment_sandbox_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
            ArtifactWriteSpec(index_id, ArtifactType.REPORT, index, "json", metadata),
        ],
        action_type=ControllerActionType.PYTHON_EXPERIMENT_SANDBOX_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "sandbox_run_id": report.sandbox_run_id,
            "execution_mode": report.execution_mode,
            "sandbox_status": report.sandbox_status,
            "network_disabled": True,
            "experiment_artifact_ingested": bool(
                report.ingested_experiment_artifact_path_optional
            ),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    reviewer = build_reviewer_bundle_summary(run_id=report.run_id, root=root)
    reviewer_id = f"reviewer-bundle-summary-after-python-sandbox-{number:04d}"
    persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                reviewer_id,
                ArtifactType.REPORT,
                reviewer,
                "json",
                {**metadata, "artifact_role": "reviewer_bundle_summary_context"},
            ),
            ArtifactWriteSpec(
                f"{reviewer_id}-markdown",
                ArtifactType.REPORT,
                render_reviewer_bundle_summary_markdown(reviewer),
                "markdown",
                {**metadata, "artifact_role": "reviewer_bundle_summary_context"},
                filename_stem=reviewer_id,
            ),
        ],
        action_type=ControllerActionType.PYTHON_EXPERIMENT_SANDBOX_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "sandbox_run_id": report.sandbox_run_id,
            "reviewer_summary_updated": True,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return PythonExperimentSandboxResult(
        run_id=report.run_id,
        report=report,
        index=index,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
    )


def _load_reports(reports: Path) -> list[PythonExperimentSandboxReport]:
    result: list[PythonExperimentSandboxReport] = []
    for path in sorted(reports.glob("python-experiment-sandbox-run-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            result.append(PythonExperimentSandboxReport.model_validate_json(path.read_text()))
        except (OSError, ValidationError):
            continue
    return result


def _next_run_number(reports: Path) -> int:
    numbers: list[int] = []
    for path in reports.glob("python-experiment-sandbox-run-*.json"):
        if path.name.endswith(".meta.json"):
            continue
        match = re.search(r"run-(\d{4})\.json$", path.name)
        if match:
            numbers.append(int(match.group(1)))
    experiments = reports.parent / "experiments"
    for path in experiments.glob("python-experiment-sandbox-run-*"):
        match = re.search(r"run-(\d{4})$", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _expected_paths(root: Path, run_id: str, sandbox_run_id: str) -> dict[str, str]:
    base = root / "runs" / run_id / "experiments" / sandbox_run_id
    return {
        "stdout": _display_path(base / "stdout.txt", root),
        "stderr": _display_path(base / "stderr.txt", root),
        "metrics": _display_path(base / "metrics.json", root),
        "manifest": _display_path(base / "artifact-manifest.json", root),
    }


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _write_text_atomic(path: Path, value: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def _write_json_atomic(path: Path, value: Any) -> None:
    _write_text_atomic(path, canonical_json(value) + "\n")


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "section"


__all__ = [
    "PythonExperimentSandboxError",
    "PythonExperimentSandboxResult",
    "inspect_python_experiment_sandbox",
    "latest_python_experiment_sandbox_report",
    "python_experiment_sandbox_summary_fields",
    "render_python_experiment_sandbox_markdown",
    "run_python_experiment_sandbox",
]
