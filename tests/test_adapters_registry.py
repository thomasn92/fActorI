from __future__ import annotations

from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.registry import (
    AdapterConfigurationError,
    get_adapter_registry,
)
from factori.cli import app


def test_fake_adapter_registry_is_deterministic() -> None:
    first = get_adapter_registry()
    second = get_adapter_registry()

    assert first == second
    assert first.class_names() == second.class_names()


def test_registry_exposes_all_required_adapters() -> None:
    registry = get_adapter_registry()

    assert registry.class_names() == {
        "llm": "FakeLLMClient",
        "retrieval": "FakeRetrievalClient",
        "proof_verifier": "FakeProofVerifier",
        "experiment_runner": "FakeExperimentRunner",
        "prose_generator": "FakeProseGenerator",
        "human_review": "FakeHumanReviewClient",
    }


def test_non_fake_backend_fails_clearly() -> None:
    try:
        get_adapter_registry(AdapterConfig(adapter_backend="real"))
    except AdapterConfigurationError as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit failure path.
        raise AssertionError("non-fake backend unexpectedly loaded")

    assert message == (
        "Adapter backend 'real' is not implemented. "
        "Only 'fake' is available in this milestone."
    )


def test_show_adapters_cli_works() -> None:
    result = CliRunner().invoke(app, ["show-adapters"])

    assert result.exit_code == 0, result.output
    assert "adapter_backend=fake" in result.output
    assert "allow_external_calls=false" in result.output
    assert "llm=FakeLLMClient" in result.output
    assert "human_review=FakeHumanReviewClient" in result.output


def test_adapters_cli_alias_works() -> None:
    result = CliRunner().invoke(app, ["adapters"])

    assert result.exit_code == 0, result.output
    assert "proof_verifier=FakeProofVerifier" in result.output


def test_invalid_adapter_backend_cli_fails_clearly() -> None:
    result = CliRunner().invoke(app, ["show-adapters", "--backend", "remote"])

    assert result.exit_code == 1
    assert "Adapter backend 'remote' is not implemented" in result.stderr
