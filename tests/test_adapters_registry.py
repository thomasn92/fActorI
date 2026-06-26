from __future__ import annotations

from typer.testing import CliRunner

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import (
    AdapterBackendNotFound,
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
)
from factori.adapters.registry import get_adapter_registry
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
        "reviewer": "FakeReviewerClient",
        "proof_verifier": "FakeProofVerifier",
        "experiment_runner": "FakeExperimentRunner",
        "prose_generator": "FakeProseGenerator",
        "human_review": "FakeHumanReviewClient",
    }
    assert registry.provider_descriptors()[0].backend_name == "fake"
    assert registry.provider_descriptors()[0].is_default is True
    assert any(
        descriptor.backend_name == "lean" and descriptor.supports_proof
        for descriptor in registry.provider_descriptors()
    )
    assert any(
        descriptor.backend_name == "local_synthetic" and descriptor.supports_experiments
        for descriptor in registry.provider_descriptors()
    )


def test_non_fake_backend_fails_clearly() -> None:
    try:
        get_adapter_registry(AdapterConfig(adapter_backend="real"))
    except AdapterBackendNotFound as exc:
        message = str(exc)
    else:  # pragma: no cover - explicit failure path.
        raise AssertionError("non-fake backend unexpectedly loaded")

    assert "Adapter backend 'real' is not implemented." in message
    assert "Available llm backends are: fake, openai, real_llm." in message


def test_real_backend_disabled_and_missing_key_use_typed_errors() -> None:
    try:
        get_adapter_registry(
            AdapterConfig(
                adapter_backend="openai",
                allow_external_calls=False,
                api_key="test-key",
            )
        )
    except AdapterExternalCallsDisabled as exc:
        assert "External calls are disabled" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("OpenAI backend unexpectedly loaded")

    try:
        get_adapter_registry(
            AdapterConfig(adapter_backend="openai", allow_external_calls=True),
            environ={},
        )
    except AdapterMissingCredentials as exc:
        assert "no API key is configured" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("OpenAI backend unexpectedly loaded without a key")


def test_show_adapters_cli_works() -> None:
    result = CliRunner().invoke(app, ["show-adapters"])

    assert result.exit_code == 0, result.output
    assert "adapter_backend=fake" in result.output
    assert "allow_external_calls=false" in result.output
    assert "llm=FakeLLMClient" in result.output
    assert "human_review=FakeHumanReviewClient" in result.output
    assert "experiment_backend=fake" in result.output
    assert "provider_descriptor=backend=fake,provider=fake,kind=all" in result.output
    assert "supports_candidate_generation=true" in result.output
    assert "backend=openai,provider=openai,kind=llm" in result.output


def test_adapters_cli_alias_works() -> None:
    result = CliRunner().invoke(app, ["adapters"])

    assert result.exit_code == 0, result.output
    assert "proof_verifier=FakeProofVerifier" in result.output


def test_invalid_adapter_backend_cli_fails_clearly() -> None:
    result = CliRunner().invoke(app, ["show-adapters", "--backend", "remote"])

    assert result.exit_code == 1
    assert "Adapter backend 'remote' is not implemented" in result.stderr


def test_invalid_proof_backend_fails_clearly() -> None:
    try:
        get_adapter_registry(AdapterConfig(proof_backend="remote-proof"))
    except AdapterBackendNotFound as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown proof backend unexpectedly loaded")

    assert "Proof backend 'remote-proof' is not implemented." in message
    assert "Available proof backends are: fake, lean, real_proof." in message


def test_invalid_experiment_backend_fails_clearly() -> None:
    try:
        get_adapter_registry(AdapterConfig(experiment_backend="remote-experiment"))
    except AdapterBackendNotFound as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown experiment backend unexpectedly loaded")

    assert "Experiment backend 'remote-experiment' is not implemented." in message
    assert "Available experiment backends are: fake, local_synthetic, real_experiment." in message
