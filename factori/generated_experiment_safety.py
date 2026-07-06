"""Static safety and contract audit for LLM-generated experiment scripts."""

from __future__ import annotations

import ast
from pathlib import Path

from factori.schemas import ExperimentCodeSafetyAudit, LLMExperimentCodeArtifact

_STDLIB_IMPORTS = {"json", "math", "random", "statistics"}
_NETWORK_IMPORTS = {
    "ftplib",
    "http",
    "httpx",
    "requests",
    "socket",
    "telnetlib",
    "urllib",
}
_PROCESS_IMPORTS = {"asyncio", "multiprocessing", "os", "shlex", "subprocess"}
_UNSAFE_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "setattr",
    "vars",
}
_PROCESS_ATTRIBUTES = {"fork", "popen", "spawn", "system"}


def audit_generated_experiment_code(
    *,
    artifact: LLMExperimentCodeArtifact,
    required_metrics: list[str],
    negative_controls_required: bool,
    allowed_dependencies: list[str],
) -> ExperimentCodeSafetyAudit:
    """Audit generated code without executing it."""
    reasons: list[str] = []
    forbidden_imports: set[str] = set()
    network_access: set[str] = set()
    filesystem_escape: set[str] = set()
    subprocess_found: set[str] = set()
    unsafe_eval_exec: set[str] = set()
    nondeterminism: set[str] = set()
    resource_risk: set[str] = set()
    allowed = _STDLIB_IMPORTS | {
        item.casefold().replace("-", "_") for item in allowed_dependencies
    }
    try:
        tree = ast.parse(artifact.code, filename=artifact.entrypoint)
    except SyntaxError as exc:
        reasons.append(f"generated Python is invalid: {exc.msg}")
        return _audit_result(
            artifact=artifact,
            reasons=reasons,
            forbidden_imports=forbidden_imports,
            network_access=network_access,
            filesystem_escape=filesystem_escape,
            subprocess_found=subprocess_found,
            unsafe_eval_exec=unsafe_eval_exec,
            nondeterminism=nondeterminism,
            resource_risk=resource_risk,
            allowed=allowed,
        )

    output_writer_found = False
    random_used = False
    random_seeded = False
    identifiers: set[str] = set()
    string_literals: set[str] = set()
    hardcoded_metrics: set[str] = set()

    if len(artifact.code.encode("utf-8")) > 100_000:
        resource_risk.add("source exceeds 100000 bytes")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [(node.module or "").split(".", 1)[0]]
        else:
            imports = []
        for name in imports:
            if name in _NETWORK_IMPORTS:
                network_access.add(name)
            if name in _PROCESS_IMPORTS:
                subprocess_found.add(name)
            if name not in allowed:
                forbidden_imports.add(name)
            if name in {"random", "numpy"}:
                random_used = True

        if isinstance(node, ast.Name):
            identifiers.add(node.id.casefold())
        elif isinstance(node, ast.FunctionDef):
            identifiers.add(node.name.casefold())
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr.casefold())
            if node.attr.startswith("__"):
                unsafe_eval_exec.add(node.attr)
            if node.attr in _PROCESS_ATTRIBUTES:
                subprocess_found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.add(node.value.casefold())
            candidate = node.value
            if candidate.startswith(("/", "~")) or ".." in Path(candidate).parts:
                filesystem_escape.add(candidate)
        elif (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and node.test.value is True
        ):
            resource_risk.add("unbounded while True loop")

        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in _UNSAFE_CALLS:
                unsafe_eval_exec.add(call_name)
            if call_name == "open":
                output_writer_found |= _validate_open_call(node, filesystem_escape)
            if call_name in {
                "random.seed",
                "random.Random",
                "np.random.seed",
                "np.random.default_rng",
            }:
                random_seeded = True
            if call_name.startswith(("random.", "np.random.", "numpy.random.")):
                random_used = True

        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in required_metrics
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, (int, float))
                ):
                    hardcoded_metrics.add(key.value)

    if forbidden_imports:
        reasons.append("forbidden imports found")
    if network_access:
        reasons.append("network imports or access found")
    if subprocess_found:
        reasons.append("process or shell access found")
    if unsafe_eval_exec:
        reasons.append("unsafe dynamic execution or introspection found")
    if filesystem_escape:
        reasons.append("filesystem escape or unauthorized file access found")
    if not output_writer_found:
        reasons.append("required output.json write was not found")
    if random_used and not random_seeded:
        nondeterminism.add("randomness is used without an explicit seed constructor")
        reasons.append("randomness is not deterministically seeded")
    if not _contains_semantic_identifier(identifiers, string_literals, "baseline"):
        reasons.append("baseline implementation marker is missing")
    if negative_controls_required and not _contains_semantic_identifier(
        identifiers, string_literals, "negative_control"
    ):
        reasons.append("negative-control implementation marker is missing")
    missing_metric_markers = sorted(
        metric
        for metric in required_metrics
        if metric.casefold() not in string_literals
    )
    if missing_metric_markers:
        reasons.append(
            "required metric output keys are missing: " + ", ".join(missing_metric_markers)
        )
    if hardcoded_metrics:
        reasons.append(
            "required metrics are hardcoded numeric literals: "
            + ", ".join(sorted(hardcoded_metrics))
        )
    if resource_risk:
        reasons.append("static resource risks found")
    return _audit_result(
        artifact=artifact,
        reasons=reasons,
        forbidden_imports=forbidden_imports,
        network_access=network_access,
        filesystem_escape=filesystem_escape,
        subprocess_found=subprocess_found,
        unsafe_eval_exec=unsafe_eval_exec,
        nondeterminism=nondeterminism,
        resource_risk=resource_risk,
        allowed=allowed,
    )


def _validate_open_call(node: ast.Call, filesystem_escape: set[str]) -> bool:
    if not node.args or not isinstance(node.args[0], ast.Constant):
        filesystem_escape.add("dynamic open path")
        return False
    path = node.args[0].value
    if path != "output.json":
        filesystem_escape.add(str(path))
        return False
    mode = "r"
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        mode = str(node.args[1].value)
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = str(keyword.value.value)
    if not any(marker in mode for marker in ("w", "x")) or "+" in mode:
        filesystem_escape.add(f"output.json mode={mode}")
        return False
    return True


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _contains_semantic_identifier(
    identifiers: set[str], string_literals: set[str], marker: str
) -> bool:
    return any(marker in value for value in identifiers | string_literals)


def _audit_result(
    *,
    artifact: LLMExperimentCodeArtifact,
    reasons: list[str],
    forbidden_imports: set[str],
    network_access: set[str],
    filesystem_escape: set[str],
    subprocess_found: set[str],
    unsafe_eval_exec: set[str],
    nondeterminism: set[str],
    resource_risk: set[str],
    allowed: set[str],
) -> ExperimentCodeSafetyAudit:
    return ExperimentCodeSafetyAudit(
        code_artifact_id=artifact.code_artifact_id,
        passed=not reasons,
        blocked=bool(reasons),
        reasons=reasons,
        forbidden_imports_found=sorted(forbidden_imports),
        network_access_found=sorted(network_access),
        filesystem_escape_found=sorted(filesystem_escape),
        subprocess_found=sorted(subprocess_found),
        unsafe_eval_exec_found=sorted(unsafe_eval_exec),
        nondeterminism_risk=sorted(nondeterminism),
        resource_risk=sorted(resource_risk),
        allowed_imports=sorted(allowed),
    )


__all__ = ["audit_generated_experiment_code"]
