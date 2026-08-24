from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from factori.adapters.llm_experiment_codegen import (
    ExperimentCodePatchEnvelope,
    ExperimentCodePatchProposal,
    ExperimentCodeTextEdit,
    OpenAILLMExperimentCodeGenerator,
    build_experiment_codegen_prompt,
    build_experiment_codegen_repair_prompt,
    parse_experiment_codegen_patch_response,
)


def _spec() -> dict[str, Any]:
    return {
        "output_contract": {
            "required_metrics": ["loss"],
            "required_payload_fields": ["metrics"],
            "required_logical_artifacts": [],
        },
        "workload_contract": {
            "execution_profile": "smoke",
            "max_replications": 1,
            "max_resamples": 1,
            "max_grid_cells": 1,
        },
        "required_role_functions": [],
    }


def _payload(*edits: ExperimentCodeTextEdit) -> dict[str, Any]:
    return ExperimentCodePatchEnvelope(
        repair=ExperimentCodePatchProposal(
            edits=list(edits),
            summary="Apply a bounded syntax repair.",
            language="python",
            entrypoint="experiment.py",
            expected_output_files=["output.json"],
            required_inputs=[],
            declared_dependencies=[],
            random_seed=17,
            timeout_seconds=30,
            network_required=False,
            filesystem_scope="sandbox_workdir_only",
            publication_ready=False,
            creates_scientific_validation=False,
        )
    ).model_dump(mode="json")


@dataclass
class _StaticTransport:
    payload: dict[str, Any]
    response_schema: dict[str, Any] | None = None

    def create_response(self, **kwargs: Any) -> dict[str, Any]:
        self.response_schema = kwargs["response_schema"]
        return self.payload


def test_openai_experiment_repair_applies_bounded_exact_text_patch() -> None:
    blocked = "def compute():\n    return (1 + 2\n"
    payload = _payload(
        ExperimentCodeTextEdit(
            old_text="    return (1 + 2\n",
            new_text="    return (1 + 2)\n",
        )
    )
    transport = _StaticTransport(payload)
    client = OpenAILLMExperimentCodeGenerator(
        api_key="test-key",
        model="test-model",
        transport=transport,
        allow_external_calls=True,
    )

    response = client.repair_code(
        spec_payload=_spec(),
        substrate_payload={"substrate_id": "substrate-1"},
        blocked_code=blocked,
        audit_payload={"reasons": ["generated Python is invalid"]},
        allowed_dependencies=[],
    )

    assert response.rejection_reasons == []
    assert response.accepted is not None
    assert response.accepted.code == "def compute():\n    return (1 + 2)\n"
    ast.parse(response.accepted.code)
    assert response.raw_response == payload
    assert transport.response_schema == ExperimentCodePatchEnvelope.model_json_schema()


def test_experiment_repair_rejects_ambiguous_and_whole_script_edits() -> None:
    blocked = "value = 1\nvalue = 1\n"
    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(ExperimentCodeTextEdit(old_text="value = 1", new_text="value = 2")),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert accepted is None
    assert reasons == ["repair edit 1 old_text must occur exactly once; found 2 occurrences"]

    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(ExperimentCodeTextEdit(old_text=blocked, new_text="value = 2\n")),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert accepted is None
    assert "repair edit 1 must not replace the complete script" in reasons


def test_experiment_repair_ignores_one_no_op_but_rejects_all_no_ops() -> None:
    blocked = "value = 1\nother = 1\n"
    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(
            ExperimentCodeTextEdit(old_text="value = 1", new_text="value = 1"),
            ExperimentCodeTextEdit(old_text="other = 1", new_text="other = 2"),
        ),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert reasons == []
    assert accepted is not None
    assert accepted.code == "value = 1\nother = 2\n"

    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(ExperimentCodeTextEdit(old_text="value = 1", new_text="value = 1")),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert accepted is None
    assert reasons == ["repair patch did not change the blocked script"]


def test_experiment_repair_rejects_patch_that_remains_invalid_python() -> None:
    blocked = "def compute():\n    return (1 + 2\n"
    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(
            ExperimentCodeTextEdit(
                old_text="def compute():",
                new_text="def calculate():",
            )
        ),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert accepted is None
    assert "repaired Python is invalid: '(' was never closed" in reasons


def test_experiment_repair_accepts_fourteen_bounded_local_edits() -> None:
    blocked = "".join(f"value_{index} = 0\n" for index in range(14))
    edits = [
        ExperimentCodeTextEdit(
            old_text=f"value_{index} = 0",
            new_text=f"value_{index} = 1",
        )
        for index in range(14)
    ]

    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(*edits),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert reasons == []
    assert accepted is not None
    assert "value_13 = 1" in accepted.code


def test_experiment_repair_accepts_eighteen_bounded_local_edits() -> None:
    blocked = "".join(f"value_{index} = 0\n" for index in range(18))
    edits = [
        ExperimentCodeTextEdit(
            old_text=f"value_{index} = 0",
            new_text=f"value_{index} = 1",
        )
        for index in range(18)
    ]

    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(*edits),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert reasons == []
    assert accepted is not None
    assert "value_17 = 1" in accepted.code


def test_experiment_repair_allows_bounded_patch_above_sixteen_kibibytes() -> None:
    old_blocks = [f"# {index}-" + ("x" * 5_995) + "\n" for index in range(3)]
    blocked = "".join(old_blocks) + "value = 0\n"
    edits = [
        ExperimentCodeTextEdit(old_text=block, new_text=f"# repaired-{index}\n")
        for index, block in enumerate(old_blocks)
    ]

    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(*edits),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert reasons == []
    assert accepted is not None
    assert "# repaired-2" in accepted.code


def test_experiment_repair_rejects_patch_above_sixty_four_kibibytes() -> None:
    old_blocks = [f"# {index}-" + ("x" * 13_095) + "\n" for index in range(5)]
    blocked = "".join(old_blocks) + "value = 0\n"
    edits = [
        ExperimentCodeTextEdit(old_text=block, new_text=f"# repaired-{index}\n")
        for index, block in enumerate(old_blocks)
    ]

    accepted, reasons = parse_experiment_codegen_patch_response(
        _payload(*edits),
        blocked_code=blocked,
        allowed_dependencies=[],
    )

    assert accepted is None
    assert reasons == ["repair edits exceed the bounded 64000-byte source-change limit"]


def test_experiment_repair_prompt_requests_local_edits() -> None:
    prompt, schema = build_experiment_codegen_repair_prompt(
        spec_payload=_spec(),
        substrate_payload={"substrate_id": "substrate-1"},
        blocked_code="value = (1\n",
        audit_payload={"reasons": ["generated Python is invalid"]},
        allowed_dependencies=[],
    )

    assert "bounded list of exact-text edits" in prompt
    assert "at most 32 edits" in prompt
    assert "at most 64,000 aggregate source bytes" in prompt
    assert "not a replacement script" in prompt
    assert "old_text must occur exactly once" in prompt
    assert "do not abort the whole experiment for one optimizer" in prompt
    assert "Boolean metric leaves are invalid" in prompt
    assert "do not hold raw samples for every cell" in prompt
    assert schema == ExperimentCodePatchEnvelope.model_json_schema()


def test_experiment_generation_prompt_retains_numerical_failures() -> None:
    prompt, _ = build_experiment_codegen_prompt(
        spec_payload=_spec(),
        substrate_payload={"substrate_id": "substrate-1"},
        allowed_dependencies=[],
    )

    assert "catch them at the smallest method/replicate/cell boundary" in prompt
    assert "Never relabel a failed fit as a valid metric" in prompt
    assert "Boolean metric leaves are invalid" in prompt
    assert "do not hold raw samples for every cell" in prompt
