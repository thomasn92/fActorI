"""Static safety and contract audit for LLM-generated experiment scripts."""

from __future__ import annotations

import ast
from pathlib import Path

from factori.schemas import ExperimentCodeSafetyAudit, LLMExperimentCodeArtifact

_STDLIB_IMPORTS = {
    "hashlib",
    "json",
    "math",
    "platform",
    "random",
    "statistics",
    "time",
}
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
_PROCESS_CALLS = {"os.fork", "os.popen", "os.system"}
_PROCESS_CALL_PREFIXES = (
    "asyncio.create_subprocess_",
    "multiprocessing.",
    "os.spawn",
    "subprocess.",
)
_UNSAFE_ATTRIBUTES = {
    "__builtins__",
    "__class__",
    "__code__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__subclasses__",
}


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
            if node.attr in _UNSAFE_ATTRIBUTES:
                unsafe_eval_exec.add(node.attr)
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
            if call_name in _PROCESS_CALLS or call_name.startswith(
                _PROCESS_CALL_PREFIXES
            ):
                subprocess_found.add(call_name)
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


def audit_generated_experiment_contract(
    *,
    artifact: LLMExperimentCodeArtifact,
    required_payload_fields: list[str],
) -> list[str]:
    """Check that generated output construction declares every required contract field."""
    try:
        tree = ast.parse(artifact.code, filename=artifact.entrypoint)
    except SyntaxError:
        return ["generated Python cannot be checked against the output contract"]
    declared_keys = {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    missing = sorted(set(required_payload_fields) - declared_keys)
    if not missing:
        return []
    return ["required output fields are not constructed: " + ", ".join(missing)]


def audit_generated_experiment_workload(
    *,
    artifact: LLMExperimentCodeArtifact,
    execution_profile: str,
    max_replications: int,
    max_resamples: int,
    max_grid_cells: int,
) -> list[str]:
    """Enforce explicit bounded workload constants before sandbox execution."""
    try:
        tree = ast.parse(artifact.code, filename=artifact.entrypoint)
    except SyntaxError:
        return ["generated Python cannot be checked against workload limits"]
    expected: dict[str, str | int] = {
        "EXECUTION_PROFILE": execution_profile,
        "REPLICATIONS": max_replications,
        "RESAMPLES": max_resamples,
        "GRID_CELLS": max_grid_cells,
    }
    values = _top_level_literal_assignments(tree)
    violations: list[str] = []
    if values.get("EXECUTION_PROFILE") != execution_profile:
        violations.append(
            f"EXECUTION_PROFILE must be the literal {execution_profile!r}"
        )
    for name in ("REPLICATIONS", "RESAMPLES", "GRID_CELLS"):
        value = values.get(name)
        limit = expected[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            violations.append(f"{name} must be a positive integer literal")
        elif value > limit:
            violations.append(f"{name}={value} exceeds configured limit {limit}")
        elif execution_profile == "full" and value != limit:
            violations.append(
                f"{name}={value} must equal configured full-profile value {limit}"
            )
    if execution_profile == "full":
        grid_config_count = _top_level_collection_lengths(tree).get("GRID_CONFIGS")
        if grid_config_count != max_grid_cells:
            violations.append(
                "GRID_CONFIGS must contain exactly GRID_CELLS full-profile configurations"
            )
        if not _name_is_iterated(tree, "GRID_CONFIGS"):
            violations.append("full-profile execution must iterate over GRID_CONFIGS")
        violations.extend(_full_profile_grid_violations(tree))
    return violations


def audit_generated_experiment_semantics(
    *,
    artifact: LLMExperimentCodeArtifact,
    required_role_functions: list[str],
) -> list[str]:
    """Check executable role structure and computed output bindings without judging science."""
    try:
        tree = ast.parse(artifact.code, filename=artifact.entrypoint)
    except SyntaxError:
        return ["generated Python cannot be checked against semantic role contracts"]
    definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    calls = {
        _call_name(node.func).split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    violations: list[str] = []
    for function_name in required_role_functions:
        if function_name not in definitions:
            violations.append(f"required role function {function_name} is not defined")
        elif function_name not in calls:
            violations.append(f"required role function {function_name} is not called")

    producers = _assigned_call_producers(tree)
    dependencies = _assigned_value_dependencies(tree)
    return_producers = _function_return_producers(tree)
    bindings = {
        "metrics": "compute_metrics",
        "baseline_summary": "run_baseline",
        "control_summary": "run_controls",
        "negative_control_summary": "run_negative_controls",
    }
    for field_name, producer in bindings.items():
        values = _dictionary_values_for_key(tree, field_name)
        if not values:
            continue
        if not any(
            _value_comes_from(
                value,
                producer,
                producers,
                dependencies,
                return_producers,
            )
            for value in values
        ):
            violations.append(
                f"output field {field_name} must come from {producer}, not a literal declaration"
            )
    for field_name in ("success_criteria_satisfied", "failure_criteria_satisfied"):
        values = _dictionary_values_for_key(tree, field_name)
        if values and all(isinstance(value, ast.Constant) for value in values):
            violations.append(f"output field {field_name} must be computed")
    return violations


def _top_level_literal_assignments(tree: ast.Module) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value.value
    return values


def _top_level_collection_lengths(tree: ast.Module) -> dict[str, int]:
    lengths: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                lengths[target.id] = len(value.elts)
    return lengths


def _name_is_iterated(tree: ast.Module, name: str) -> bool:
    iterators = [
        node.iter
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
    ]
    return any(
        isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id == name
        for iterator in iterators
        for child in ast.walk(iterator)
    )


def _full_profile_grid_violations(tree: ast.Module) -> list[str]:
    bindings = _grid_config_bindings(tree)
    if not bindings:
        return []
    violations: list[str] = []
    capped_keys = {
        key
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node.func).split(".")[-1] in {"min", "max"}
        for argument in node.args
        if not isinstance(
            argument, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)
        )
        for key in _grid_subscript_keys(argument, bindings)
    }
    if capped_keys:
        violations.append(
            "full-profile GRID_CONFIGS values must not be silently capped with min/max: "
            + ", ".join(sorted(capped_keys))
        )

    configs = _literal_grid_configs(tree)
    varying_keys = {
        key
        for key in set().union(*(config.keys() for config in configs))
        if len({_hashable_literal(config.get(key)) for config in configs}) > 1
    }
    dispatch_keys = {
        key
        for key in varying_keys
        if any(
            marker in key.casefold()
            for marker in ("algorithm", "estimator", "method", "model")
        )
    }
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    decorative = [
        key
        for key in sorted(dispatch_keys)
        if not any(
            _grid_access_is_computational(node, parents)
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and _grid_subscript_key(node, bindings) == key
        )
    ]
    if decorative:
        violations.append(
            "varying full-profile model/method grid values require computational dispatch, "
            "not metadata labels only: "
            + ", ".join(decorative)
        )
    return violations


def _grid_config_bindings(tree: ast.Module) -> set[str]:
    bindings: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.comprehension)):
            continue
        iterator = node.iter
        if isinstance(iterator, ast.Name) and iterator.id == "GRID_CONFIGS":
            bindings.update(_assignment_target_names(node.target))
        elif (
            isinstance(iterator, ast.Call)
            and _call_name(iterator.func).split(".")[-1] == "enumerate"
            and iterator.args
            and isinstance(iterator.args[0], ast.Name)
            and iterator.args[0].id == "GRID_CONFIGS"
        ):
            names = list(_assignment_target_names(node.target))
            if isinstance(node.target, (ast.List, ast.Tuple)) and node.target.elts:
                bindings.update(_assignment_target_names(node.target.elts[-1]))
            elif names:
                bindings.add(names[-1])
    return bindings


def _literal_grid_configs(tree: ast.Module) -> list[dict[str, object]]:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "GRID_CONFIGS"
            for target in targets
        ):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            return []
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    return []


def _grid_subscript_keys(node: ast.AST, bindings: set[str]) -> set[str]:
    return {
        key
        for child in ast.walk(node)
        if isinstance(child, ast.Subscript)
        if (key := _grid_subscript_key(child, bindings)) is not None
    }


def _grid_subscript_key(node: ast.Subscript, bindings: set[str]) -> str | None:
    if not isinstance(node.value, ast.Name) or node.value.id not in bindings:
        return None
    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return None


def _grid_access_is_computational(
    node: ast.Subscript, parents: dict[ast.AST, ast.AST]
) -> bool:
    current: ast.AST = node
    inside_literal = False
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Dict):
            inside_literal = True
        if isinstance(current, (ast.Compare, ast.IfExp, ast.MatchValue)):
            return not inside_literal
        if isinstance(current, ast.Call):
            return not inside_literal and _call_name(current.func).split(".")[-1] not in {
                "append",
                "extend",
                "update",
            }
        if isinstance(current, ast.stmt):
            return False
    return False


def _hashable_literal(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable_literal(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_hashable_literal(item) for item in value)
    return value


def _assigned_call_producers(tree: ast.AST) -> dict[str, set[str]]:
    producers: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        producer = _call_name(node.value.func).split(".")[-1]
        for target in node.targets:
            for name in _assignment_target_names(target):
                producers.setdefault(name, set()).add(producer)
    return producers


def _assignment_target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.List, ast.Tuple)):
        return {
            name
            for element in target.elts
            for name in _assignment_target_names(element)
        }
    return set()


def _assigned_value_dependencies(tree: ast.AST) -> dict[str, set[str]]:
    dependencies: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        referenced_names = {
            child.id
            for child in ast.walk(value)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        for target in targets:
            if isinstance(target, ast.Name):
                dependencies[target.id] = referenced_names
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr not in {"append", "extend"}
            or not isinstance(node.func.value, ast.Name)
        ):
            continue
        referenced_names = {
            child.id
            for argument in node.args
            for child in ast.walk(argument)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        dependencies.setdefault(node.func.value.id, set()).update(referenced_names)
    return dependencies


def _function_return_producers(tree: ast.Module) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        assigned = _assigned_call_producers(function)
        dependencies = _assigned_value_dependencies(function)
        returned: set[str] = set()
        for return_node in (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and node.value is not None
        ):
            returned.update(
                _call_name(node.func).split(".")[-1]
                for node in ast.walk(return_node.value)
                if isinstance(node, ast.Call)
            )
            pending = [
                node.id
                for node in ast.walk(return_node.value)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            ]
            visited: set[str] = set()
            while pending:
                name = pending.pop()
                if name in visited:
                    continue
                visited.add(name)
                if name in assigned:
                    returned.update(assigned[name])
                pending.extend(dependencies.get(name, set()) - visited)
        graph[function.name] = returned
    return graph


def _dictionary_values_for_key(tree: ast.Module, field_name: str) -> list[ast.expr]:
    return [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == field_name
    ]


def _value_comes_from(
    value: ast.expr,
    producer: str,
    producers: dict[str, set[str]],
    dependencies: dict[str, set[str]],
    return_producers: dict[str, set[str]],
) -> bool:
    if any(
        isinstance(node, ast.Call)
        and _producer_reaches(
            _call_name(node.func).split(".")[-1], producer, return_producers
        )
        for node in ast.walk(value)
    ):
        return True
    referenced_names = {
        node.id
        for node in ast.walk(value)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    pending = list(referenced_names)
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        if name == producer:
            return True
        assigned_producers = producers.get(name, set())
        if any(
            _producer_reaches(item, producer, return_producers)
            for item in assigned_producers
        ):
            return True
        pending.extend(dependencies.get(name, set()) - visited)
    return False


def _producer_reaches(
    candidate: str,
    expected: str,
    return_producers: dict[str, set[str]],
) -> bool:
    pending = [candidate]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        if name == expected:
            return True
        pending.extend(return_producers.get(name, set()) - visited)
    return False


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


__all__ = [
    "audit_generated_experiment_code",
    "audit_generated_experiment_contract",
    "audit_generated_experiment_semantics",
    "audit_generated_experiment_workload",
]
