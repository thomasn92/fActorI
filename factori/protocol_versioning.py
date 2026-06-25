"""Protocol version-bump rules for fActorI JSON Schema contracts."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field

from factori.protocol_compat import (
    ProtocolCompatibilityReport,
    ProtocolCompatibilityStatus,
    compare_schema_dirs,
)
from factori.schema_diff import SchemaChangeClassification
from factori.schemas import StrictModel


class ProtocolVersionBump(StrEnum):
    """Minimum semantic-version movement required by schema changes."""

    NONE = "None"
    PATCH = "Patch"
    MINOR = "Minor"
    MAJOR = "Major"
    HUMAN_REVIEW = "HumanReview"


class ProtocolVersionCheckStatus(StrEnum):
    """Outcome of a protocol versioning check."""

    PASSED = "Passed"
    FAILED = "Failed"
    HUMAN_REVIEW_REQUIRED = "HumanReviewRequired"


class ProtocolVersioningReport(StrictModel):
    """Read-only protocol-version check result."""

    old_version: str = Field(min_length=1)
    new_version: str = Field(min_length=1)
    required_bump: ProtocolVersionBump
    observed_bump: ProtocolVersionBump
    status: ProtocolVersionCheckStatus
    reasons: list[str] = Field(default_factory=list)
    compatibility_status: ProtocolCompatibilityStatus
    breaking_changes: int = Field(ge=0)
    nonbreaking_changes: int = Field(ge=0)
    documentation_changes: int = Field(ge=0)
    unknown_changes: int = Field(ge=0)
    read_only: bool = True
    developer_contract_only: bool = True
    is_verification_evidence: bool = False


def check_protocol_version_bump(
    compatibility_report: ProtocolCompatibilityReport,
    *,
    old_version: str | None = None,
    new_version: str | None = None,
    allow_unknown: bool = False,
) -> ProtocolVersioningReport:
    """Check whether a protocol version bump matches conservative compatibility rules."""
    old = old_version or compatibility_report.old_protocol_version
    new = new_version or compatibility_report.new_protocol_version
    required = required_version_bump(compatibility_report)
    observed = observed_version_bump(old, new)
    reasons: list[str] = []

    if compatibility_report.compatibility_status == ProtocolCompatibilityStatus.COMPARISON_FAILED:
        reasons.append("Schema comparison failed; version compatibility cannot be evaluated.")
        return _report(
            compatibility_report,
            old,
            new,
            required,
            observed,
            ProtocolVersionCheckStatus.FAILED,
            reasons,
        )

    if required == ProtocolVersionBump.HUMAN_REVIEW and not allow_unknown:
        reasons.append("Unknown schema changes require human review.")
        return _report(
            compatibility_report,
            old,
            new,
            required,
            observed,
            ProtocolVersionCheckStatus.HUMAN_REVIEW_REQUIRED,
            reasons,
        )
    if required == ProtocolVersionBump.HUMAN_REVIEW and allow_unknown:
        required = ProtocolVersionBump.MINOR
        reasons.append("Unknown schema changes were explicitly allowed for this check.")

    if not _bump_satisfies(observed, required):
        reasons.append(
            f"Observed version bump {observed.value} does not satisfy required "
            f"{required.value} bump."
        )
        status = ProtocolVersionCheckStatus.FAILED
    else:
        status = ProtocolVersionCheckStatus.PASSED
    return _report(compatibility_report, old, new, required, observed, status, reasons)


def check_protocol_version_dirs(
    old_dir,
    new_dir,
    *,
    old_version: str | None = None,
    new_version: str | None = None,
    allow_unknown: bool = False,
) -> ProtocolVersioningReport:
    """Compare schema directories and validate the requested protocol version movement."""
    report = compare_schema_dirs(old_dir, new_dir)
    return check_protocol_version_bump(
        report,
        old_version=old_version,
        new_version=new_version,
        allow_unknown=allow_unknown,
    )


def required_version_bump(report: ProtocolCompatibilityReport) -> ProtocolVersionBump:
    """Return the minimum version bump required by a compatibility report."""
    if report.compatibility_status == ProtocolCompatibilityStatus.COMPARISON_FAILED:
        return ProtocolVersionBump.HUMAN_REVIEW
    if report.breaking_changes:
        return ProtocolVersionBump.MAJOR
    if report.unknown_changes:
        return ProtocolVersionBump.HUMAN_REVIEW
    structural_nonbreaking = [
        change
        for change in report.nonbreaking_changes
        if change.classification == SchemaChangeClassification.NON_BREAKING
    ]
    if report.schemas_added or structural_nonbreaking:
        return ProtocolVersionBump.MINOR
    if report.documentation_changes:
        return ProtocolVersionBump.PATCH
    return ProtocolVersionBump.NONE


def observed_version_bump(old_version: str, new_version: str) -> ProtocolVersionBump:
    """Classify the observed semantic-version movement."""
    old = _parse_semver(old_version)
    new = _parse_semver(new_version)
    if old is None or new is None or new < old:
        return ProtocolVersionBump.HUMAN_REVIEW
    if new[0] > old[0]:
        return ProtocolVersionBump.MAJOR
    if new[1] > old[1]:
        return ProtocolVersionBump.MINOR
    if new[2] > old[2]:
        return ProtocolVersionBump.PATCH
    return ProtocolVersionBump.NONE


def _bump_satisfies(observed: ProtocolVersionBump, required: ProtocolVersionBump) -> bool:
    order = {
        ProtocolVersionBump.NONE: 0,
        ProtocolVersionBump.PATCH: 1,
        ProtocolVersionBump.MINOR: 2,
        ProtocolVersionBump.MAJOR: 3,
    }
    if required == ProtocolVersionBump.HUMAN_REVIEW:
        return False
    if observed == ProtocolVersionBump.HUMAN_REVIEW:
        return False
    return order[observed] >= order[required]


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _report(
    compatibility_report: ProtocolCompatibilityReport,
    old_version: str,
    new_version: str,
    required: ProtocolVersionBump,
    observed: ProtocolVersionBump,
    status: ProtocolVersionCheckStatus,
    reasons: list[str],
) -> ProtocolVersioningReport:
    return ProtocolVersioningReport(
        old_version=old_version,
        new_version=new_version,
        required_bump=required,
        observed_bump=observed,
        status=status,
        reasons=sorted(reasons),
        compatibility_status=compatibility_report.compatibility_status,
        breaking_changes=len(compatibility_report.breaking_changes),
        nonbreaking_changes=len(compatibility_report.nonbreaking_changes),
        documentation_changes=len(compatibility_report.documentation_changes),
        unknown_changes=len(compatibility_report.unknown_changes),
    )


__all__ = [
    "ProtocolVersionBump",
    "ProtocolVersionCheckStatus",
    "ProtocolVersioningReport",
    "check_protocol_version_bump",
    "check_protocol_version_dirs",
    "observed_version_bump",
    "required_version_bump",
]
