"""Provider-neutral adapter capability descriptors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterCapability:
    """One named adapter capability used for fail-closed validation."""

    name: str
    description: str


@dataclass(frozen=True)
class AdapterProviderDescriptor:
    """Provider-neutral description of a registered adapter backend."""

    backend_name: str
    provider_name: str
    adapter_kind: str
    supports_candidate_generation: bool = False
    supports_review: bool = False
    supports_retrieval: bool = False
    supports_proof: bool = False
    supports_experiments: bool = False
    requires_external_calls: bool = False
    requires_external_tools: bool = False
    requires_api_key: bool = False
    is_default: bool = False
    is_fake: bool = False
    aliases: tuple[str, ...] = ()

    def all_backend_names(self) -> tuple[str, ...]:
        """Return canonical backend plus accepted aliases."""
        return (self.backend_name, *self.aliases)

    def supports(self, capability: str) -> bool:
        """Return whether this descriptor supports a named capability."""
        capability_flags = {
            "candidate_generation": self.supports_candidate_generation,
            "review": self.supports_review,
            "retrieval": self.supports_retrieval,
            "proof": self.supports_proof,
            "experiments": self.supports_experiments,
        }
        return capability_flags.get(capability, False)


@dataclass(frozen=True)
class AdapterRegistryDescriptor:
    """Deterministic provider descriptor collection for inspection and protocols."""

    active_candidate_backend: str
    active_reviewer_backend: str
    active_retrieval_backend: str
    active_proof_backend: str
    allow_external_calls: bool
    allow_external_tools: bool
    providers: tuple[AdapterProviderDescriptor, ...]


FAKE_DESCRIPTOR = AdapterProviderDescriptor(
    backend_name="fake",
    provider_name="fake",
    adapter_kind="all",
    supports_candidate_generation=True,
    supports_review=True,
    supports_retrieval=True,
    supports_proof=True,
    supports_experiments=True,
    requires_external_calls=False,
    requires_external_tools=False,
    requires_api_key=False,
    is_default=True,
    is_fake=True,
)
OPENAI_CANDIDATE_DESCRIPTOR = AdapterProviderDescriptor(
    backend_name="openai",
    provider_name="openai",
    adapter_kind="llm",
    supports_candidate_generation=True,
    requires_external_calls=True,
    requires_external_tools=False,
    requires_api_key=True,
    aliases=("real_llm",),
)
OPENAI_REVIEWER_DESCRIPTOR = AdapterProviderDescriptor(
    backend_name="openai",
    provider_name="openai",
    adapter_kind="reviewer",
    supports_review=True,
    requires_external_calls=True,
    requires_external_tools=False,
    requires_api_key=True,
    aliases=("real_llm",),
)
OPENALEX_RETRIEVAL_DESCRIPTOR = AdapterProviderDescriptor(
    backend_name="openalex",
    provider_name="openalex",
    adapter_kind="retrieval",
    supports_retrieval=True,
    requires_external_calls=True,
    requires_external_tools=False,
    requires_api_key=True,
    aliases=("real_retrieval",),
)
LEAN_PROOF_DESCRIPTOR = AdapterProviderDescriptor(
    backend_name="lean",
    provider_name="lean",
    adapter_kind="proof",
    supports_proof=True,
    requires_external_calls=False,
    requires_external_tools=True,
    requires_api_key=False,
    aliases=("real_proof",),
)

PROVIDER_DESCRIPTORS = (
    FAKE_DESCRIPTOR,
    OPENAI_CANDIDATE_DESCRIPTOR,
    OPENAI_REVIEWER_DESCRIPTOR,
    OPENALEX_RETRIEVAL_DESCRIPTOR,
    LEAN_PROOF_DESCRIPTOR,
)


def get_provider_descriptors() -> tuple[AdapterProviderDescriptor, ...]:
    """Return all registered provider descriptors in stable order."""
    return PROVIDER_DESCRIPTORS


def descriptors_for_kind(kind: str) -> tuple[AdapterProviderDescriptor, ...]:
    """Return descriptors matching an adapter kind plus fake all-capability backend."""
    normalized = kind.strip().lower()
    return tuple(
        descriptor
        for descriptor in PROVIDER_DESCRIPTORS
        if descriptor.adapter_kind in {normalized, "all"}
    )


def backend_names_for_kind(kind: str) -> tuple[str, ...]:
    """Return accepted backend names and aliases for a kind."""
    names: list[str] = []
    for descriptor in descriptors_for_kind(kind):
        for name in descriptor.all_backend_names():
            if name not in names:
                names.append(name)
    return tuple(names)


def find_descriptor(
    backend_name: str,
    *,
    kind: str,
    capability: str | None = None,
) -> AdapterProviderDescriptor | None:
    """Find a descriptor by backend alias, kind, and optional capability."""
    normalized_backend = backend_name.strip().lower()
    for descriptor in descriptors_for_kind(kind):
        if normalized_backend not in descriptor.all_backend_names():
            continue
        if capability is not None and not descriptor.supports(capability):
            continue
        return descriptor
    return None


def known_retrieval_providers() -> frozenset[str]:
    """Return provider names accepted by retrieval safety validation."""
    return frozenset(
        descriptor.provider_name
        for descriptor in PROVIDER_DESCRIPTORS
        if descriptor.supports_retrieval
    )


__all__ = [
    "AdapterCapability",
    "AdapterProviderDescriptor",
    "AdapterRegistryDescriptor",
    "FAKE_DESCRIPTOR",
    "LEAN_PROOF_DESCRIPTOR",
    "OPENAI_CANDIDATE_DESCRIPTOR",
    "OPENAI_REVIEWER_DESCRIPTOR",
    "OPENALEX_RETRIEVAL_DESCRIPTOR",
    "PROVIDER_DESCRIPTORS",
    "backend_names_for_kind",
    "descriptors_for_kind",
    "find_descriptor",
    "get_provider_descriptors",
    "known_retrieval_providers",
]
