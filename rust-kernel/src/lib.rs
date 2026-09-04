//! Protocol and canonical-hash boundary for the future Rust kernel.
//!
//! The kernel keeps evidence and authority boundaries explicit. Mutating operations are limited
//! to the frozen `artifact.persist`, `artifact.link`, transactional artifact-free
//! `ledger.append`, and crash-recoverable `persistence.commit_bundle` contracts;
//! all other operations remain read-only integrity checks.

use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::Deserializer;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OpenFlags};

pub const PROTOCOL_VERSION: &str = "0.90.0";
pub const KERNEL_VERSION: &str = "0.1.0-dev";
const DEFAULT_FORBIDDEN_PROOF_TOKENS: [&str; 4] = ["sorry", "admit", "axiom", "unsafe"];
const SUPPORTED_SYNTHETIC_EXPERIMENT_KINDS: [&str; 4] = [
    "SyntheticSimulation",
    "SyntheticAblation",
    "SyntheticRobustnessCheck",
    "NoDataSanityCheck",
];

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct KernelRequest {
    pub protocol_version: String,
    pub request_id: String,
    pub operation: KernelOperation,
    pub mode: KernelMode,
    pub payload: Map<String, Value>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct LedgerVerifyPayload {
    run_id: String,
    commits: Vec<WireLedgerCommit>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactVerifyPayload {
    run_id: String,
    artifact: WireArtifactRef,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct EvidenceClassifyPayload {
    run_id: String,
    artifact: WireArtifactRef,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct EvidenceValidateBundlePayload {
    run_id: String,
    candidate_id: String,
    claim_id: String,
    producing_commit_hash: String,
    bundle: EvidenceBundle,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimEvidenceLocator {
    producing_commit_hash: String,
    bundle: EvidenceBundle,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimTableLocator {
    artifact_id: String,
    producing_commit_hash: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimResolvePayload {
    run_id: String,
    claim_id: String,
    claim_table: ClaimTableLocator,
    evidence: Option<ClaimEvidenceLocator>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckpointIndexLocator {
    artifact_id: String,
    producing_commit_hash: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckpointVerifyPayload {
    run_id: String,
    index: CheckpointIndexLocator,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayVerifyCorePayload {
    run_id: String,
    ledger_tip_hash: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactPersistPayload {
    run_id: String,
    artifact_id: String,
    artifact_type: String,
    json_value: Value,
    #[serde(default)]
    metadata: Map<String, Value>,
    #[serde(default)]
    filename_stem_optional: Option<String>,
    overwrite_policy: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct LedgerAppendPayload {
    run_id: String,
    expected_tip_hash: String,
    action_type: String,
    payload: Map<String, Value>,
    #[serde(default)]
    candidate_id_optional: Option<String>,
    timestamp: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactLinkPayload {
    run_id: String,
    expected_ledger_tip_hash: String,
    artifact: WireArtifactRef,
    producing_commit_hash: String,
    overwrite_policy: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CommitBundleArtifact {
    artifact_id: String,
    artifact_type: String,
    json_value: Value,
    #[serde(default)]
    metadata: Map<String, Value>,
    #[serde(default)]
    filename_stem_optional: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PersistenceCommitBundlePayload {
    run_id: String,
    expected_tip_hash: String,
    artifacts: Vec<CommitBundleArtifact>,
    action_type: String,
    commit_payload: Map<String, Value>,
    #[serde(default)]
    candidate_id_optional: Option<String>,
    timestamp: String,
    overwrite_policy: String,
    recovery_policy: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactManifestWire {
    run_id: String,
    artifacts: Vec<ArtifactManifestEntryWire>,
    evidence_artifact_count: usize,
    presentation_artifact_count: usize,
    source_of_truth: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactManifestEntryWire {
    artifact_id: String,
    artifact_type: String,
    path: String,
    content_hash: Option<String>,
    producing_commit_hash: Option<String>,
    is_evidence: bool,
    is_presentation: bool,
    #[serde(default)]
    metadata: Map<String, Value>,
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckpointWire {
    run_id: String,
    controller_run_id: String,
    stage_name: String,
    stage_status: String,
    stage_artifact_paths: Vec<String>,
    stage_started_at: String,
    stage_completed_at: String,
    protocol_version: String,
    ledger_tip_hash_optional: Option<String>,
    checkpoint_hash: String,
    input_hashes: BTreeMap<String, String>,
    output_hashes: BTreeMap<String, String>,
    safety_gate_status: String,
    release_status_optional: Option<String>,
    publication_ready: bool,
    verified_for_resume: bool,
    verification_status: String,
    verification_errors: Vec<String>,
    creates_scientific_validation: bool,
    implies_publication_readiness: bool,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckpointIndexWire {
    run_id: String,
    latest_controller_run_id: String,
    checkpoint_count: usize,
    latest_completed_stage: Option<String>,
    checkpoints: Vec<String>,
    resume_allowed: bool,
    resume_blockers: Vec<String>,
    publication_ready: bool,
    creates_scientific_validation: bool,
    implies_publication_readiness: bool,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimEvidenceLinkWire {
    claim_id: String,
    artifact_id: String,
    artifact_type: String,
    evidence_role: Option<String>,
    supports_label: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimWire {
    claim_id: String,
    claim_text: String,
    claim_label: String,
    candidate_id: String,
    evidence_artifact_ids: Vec<String>,
    evidence_types: Vec<String>,
    allowed_in_main_text: bool,
    allowed_section: String,
    reason: String,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClaimTableWire {
    final_nucleus_id: String,
    claims: Vec<ClaimWire>,
    evidence_links: Vec<ClaimEvidenceLinkWire>,
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
#[serde(tag = "kind")]
enum EvidenceBundle {
    #[serde(rename = "LeanProof")]
    Lean {
        contract_artifact_id: String,
        payload_artifact_id: String,
        trace_artifact_id: String,
        result_artifact_id: String,
        safety_artifact_id: String,
    },
    #[serde(rename = "SyntheticExperiment")]
    Synthetic {
        contract_artifact_id: String,
        input_artifact_id: String,
        trace_artifact_id: String,
        output_artifact_id: String,
        result_artifact_id: String,
        safety_artifact_id: String,
    },
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ProofContractWire {
    candidate_id: String,
    claim_id: String,
    claim_text: String,
    proof_language: String,
    proof_payload_path: Option<String>,
    proof_payload_text: Option<String>,
    proof_payload: Map<String, Value>,
    allowed_imports: Vec<String>,
    forbidden_tokens: Vec<String>,
    timeout_seconds: i64,
    expected_output_type: String,
    backend: String,
    tool_name: Option<String>,
    allow_external_calls: bool,
    allow_external_tools: bool,
    fake_default: bool,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ProofPayloadWire {
    candidate_id: String,
    claim_id: String,
    proof_language: String,
    proof_payload_text: String,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ProofTraceWire {
    backend: String,
    provider: String,
    tool_name: String,
    exit_code: i64,
    stdout: String,
    stderr: String,
    elapsed_ms: u64,
    tool_version: Option<String>,
    fake: bool,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ProofResultWire {
    candidate_id: String,
    claim_id: String,
    backend: String,
    provider: String,
    proof_language: String,
    tool_name: String,
    tool_version: Option<String>,
    exit_code: i64,
    stdout_hash: String,
    stderr_hash: String,
    proof_payload_hash: String,
    forbidden_tokens_present: bool,
    verified: bool,
    label: String,
    reason: String,
    elapsed_ms: Option<u64>,
    raw_trace_artifact_id: Option<String>,
    safety_report_artifact_id: Option<String>,
    fake: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ProofSafetyWire {
    candidate_id: String,
    claim_id: String,
    contract_valid: bool,
    contract_reasons: Vec<String>,
    result_valid: bool,
    result_reasons: Vec<String>,
    is_verification_evidence: bool,
    fake: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyntheticContractWire {
    candidate_id: String,
    claim_id: String,
    experiment_id: String,
    experiment_kind: String,
    data_regime: String,
    synthetic_data_spec: Map<String, Value>,
    model_spec: Map<String, Value>,
    algorithm_spec: Map<String, Value>,
    metrics: Vec<String>,
    acceptance_criteria: Map<String, Value>,
    random_seed: i64,
    replications: i64,
    timeout_seconds: i64,
    backend: String,
    runner_name: Option<String>,
    forbidden_external_inputs: Vec<String>,
    expected_output_type: String,
    allow_external_calls: bool,
    allow_external_tools: bool,
    fake_default: bool,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyntheticInputWire {
    candidate_id: String,
    claim_id: String,
    experiment_id: String,
    experiment_kind: String,
    data_regime: String,
    synthetic_data_spec: Map<String, Value>,
    model_spec: Map<String, Value>,
    algorithm_spec: Map<String, Value>,
    metrics: Vec<String>,
    acceptance_criteria: Map<String, Value>,
    random_seed: i64,
    replications: i64,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyntheticTraceWire {
    backend: String,
    provider: String,
    runner_name: String,
    exit_code: i64,
    stdout: String,
    stderr: String,
    elapsed_ms: u64,
    runner_version: Option<String>,
    fake: bool,
    is_verification_evidence: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyntheticOutputWire {
    metrics: Map<String, Value>,
    #[serde(default)]
    synthetic_only: Option<bool>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyntheticResultWire {
    candidate_id: String,
    claim_id: String,
    experiment_id: String,
    backend: String,
    provider: String,
    experiment_kind: String,
    data_regime: String,
    runner_name: String,
    runner_version: Option<String>,
    exit_code: i64,
    stdout_hash: String,
    stderr_hash: String,
    input_spec_hash: String,
    output_payload_hash: String,
    metrics: Map<String, Value>,
    acceptance_criteria: Map<String, Value>,
    passed: bool,
    label: String,
    reason: String,
    elapsed_ms: Option<u64>,
    raw_trace_artifact_id: Option<String>,
    safety_report_artifact_id: Option<String>,
    fake: bool,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyntheticSafetyWire {
    candidate_id: String,
    claim_id: String,
    contract_valid: bool,
    contract_reasons: Vec<String>,
    result_valid: bool,
    result_reasons: Vec<String>,
    is_verification_evidence: bool,
    fake: bool,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct WireArtifactRef {
    id: String,
    #[serde(rename = "type")]
    artifact_type: String,
    path: String,
    content_hash: String,
    #[serde(default)]
    producing_commit_hash: Option<String>,
    #[serde(default)]
    metadata: Map<String, Value>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct WireLedgerCommit {
    commit_hash: String,
    #[serde(default)]
    parent_hash: Option<String>,
    run_id: String,
    #[serde(default)]
    candidate_id: Option<String>,
    action_type: String,
    #[serde(default)]
    payload: Map<String, Value>,
    #[serde(default)]
    artifact_refs: Vec<Value>,
    timestamp: String,
}

#[derive(Debug, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub enum KernelOperation {
    #[serde(rename = "protocol.validate")]
    ProtocolValidate,
    #[serde(rename = "hash.canonical_json")]
    HashCanonicalJson,
    #[serde(rename = "artifact.verify")]
    ArtifactVerify,
    #[serde(rename = "ledger.verify")]
    LedgerVerify,
    #[serde(rename = "evidence.classify")]
    EvidenceClassify,
    #[serde(rename = "evidence.validate_bundle")]
    EvidenceValidateBundle,
    #[serde(rename = "claim.resolve")]
    ClaimResolve,
    #[serde(rename = "checkpoint.verify")]
    CheckpointVerify,
    #[serde(rename = "replay.verify_core")]
    ReplayVerifyCore,
    #[serde(rename = "artifact.persist")]
    ArtifactPersist,
    #[serde(rename = "ledger.append")]
    LedgerAppend,
    #[serde(rename = "artifact.link")]
    ArtifactLink,
    #[serde(rename = "persistence.commit_bundle")]
    PersistenceCommitBundle,
}

#[derive(Debug, serde::Serialize, serde::Deserialize, Clone, Copy, PartialEq, Eq)]
pub enum KernelMode {
    #[serde(rename = "DevelopmentCompatibility")]
    DevelopmentCompatibility,
    #[serde(rename = "StrictProduction")]
    StrictProduction,
}

#[derive(Debug, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum KernelResponseStatus {
    Accepted,
    Rejected,
    Error,
}

#[derive(Debug, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct KernelDiagnostic {
    pub code: String,
    pub message: String,
    pub path: Option<String>,
}

#[derive(Debug, serde::Serialize)]
pub struct KernelResponse {
    pub protocol_version: &'static str,
    pub kernel_version: &'static str,
    pub request_id: String,
    pub operation: KernelOperation,
    pub mode: KernelMode,
    pub status: KernelResponseStatus,
    pub result: Map<String, Value>,
    pub diagnostics: Vec<KernelDiagnostic>,
    pub mutation_performed: bool,
}

type KernelOperationError = (&'static str, String, Option<&'static str>);
type EvidenceClassification = (Map<String, Value>, Vec<KernelDiagnostic>);

#[derive(Debug, PartialEq, Eq)]
pub enum KernelError {
    InvalidRequest(String),
    InvalidPayload(String),
    UnsupportedProtocol(String),
    InvalidJson(String),
}

impl std::fmt::Display for KernelError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidRequest(message)
            | Self::InvalidPayload(message)
            | Self::UnsupportedProtocol(message)
            | Self::InvalidJson(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for KernelError {}

pub fn canonical_json(value: &Value) -> Result<String, KernelError> {
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => serde_json::to_string(value)
            .map_err(|error| KernelError::InvalidJson(error.to_string())),
        Value::Number(number) => canonical_number(number),
        Value::Array(items) => {
            let rendered = items
                .iter()
                .map(canonical_json)
                .collect::<Result<Vec<_>, _>>()?;
            Ok(format!("[{}]", rendered.join(",")))
        }
        Value::Object(object) => {
            let sorted: BTreeMap<&String, &Value> = object.iter().collect();
            let mut rendered = Vec::with_capacity(sorted.len());
            for (key, child) in sorted {
                let encoded_key = serde_json::to_string(key)
                    .map_err(|error| KernelError::InvalidJson(error.to_string()))?;
                rendered.push(format!("{}:{}", encoded_key, canonical_json(child)?));
            }
            Ok(format!("{{{}}}", rendered.join(",")))
        }
    }
}

fn canonical_number(number: &serde_json::Number) -> Result<String, KernelError> {
    let raw = number.to_string();
    if !raw.contains('.') && !raw.contains('e') && !raw.contains('E') {
        let (negative, digits) = raw
            .strip_prefix('-')
            .map_or((false, raw.as_str()), |value| (true, value));
        let normalized = digits.trim_start_matches('0');
        if normalized.is_empty() {
            return Ok("0".to_owned());
        }
        return Ok(if negative {
            format!("-{normalized}")
        } else {
            normalized.to_owned()
        });
    }

    let parsed = raw
        .parse::<f64>()
        .map_err(|error| KernelError::InvalidJson(error.to_string()))?;
    if !parsed.is_finite() {
        return Err(KernelError::InvalidJson(
            "non-finite JSON number".to_owned(),
        ));
    }
    let mut buffer = ryu::Buffer::new();
    let rendered = buffer.format_finite(parsed);
    Ok(normalize_python_float(rendered))
}

fn normalize_python_float(rendered: &str) -> String {
    let negative = rendered.starts_with('-');
    let unsigned = rendered.strip_prefix('-').unwrap_or(rendered);
    let (mantissa, exponent) = if let Some((mantissa, exponent)) = unsigned.split_once('e') {
        let exponent = exponent
            .parse::<i32>()
            .expect("ryu emits a decimal exponent");
        let digits_before_decimal = mantissa.find('.').unwrap_or(mantissa.len()) as i32;
        (
            mantissa.replace('.', ""),
            digits_before_decimal + exponent - 1,
        )
    } else {
        let digits_before_decimal = unsigned.find('.').unwrap_or(unsigned.len()) as i32;
        let digits = unsigned.replace('.', "");
        let leading_zeroes = digits.len() - digits.trim_start_matches('0').len();
        if leading_zeroes == digits.len() {
            return rendered.to_owned();
        }
        (
            digits[leading_zeroes..].to_owned(),
            digits_before_decimal - leading_zeroes as i32 - 1,
        )
    };
    if (-4..16).contains(&exponent) {
        let decimal_position = exponent + 1;
        let mut digits = mantissa;
        let fixed = if decimal_position <= 0 {
            format!("0.{}{}", "0".repeat((-decimal_position) as usize), digits)
        } else if decimal_position >= digits.len() as i32 {
            digits.push_str(&"0".repeat(decimal_position as usize - digits.len()));
            if !digits.contains('.') {
                digits.push_str(".0");
            }
            digits
        } else {
            let position = decimal_position as usize;
            format!("{}.{}", &digits[..position], &digits[position..])
        };
        return if negative { format!("-{fixed}") } else { fixed };
    }

    let sign = if exponent < 0 { '-' } else { '+' };
    let mantissa = if mantissa.len() == 1 {
        mantissa
    } else {
        format!("{}.{}", &mantissa[..1], &mantissa[1..])
    };
    let rendered = format!("{mantissa}e{sign}{:02}", exponent.abs());
    if negative {
        format!("-{rendered}")
    } else {
        rendered
    }
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn handle_request(request: KernelRequest, project_root: Option<&Path>) -> KernelResponse {
    let request_id = request.request_id;
    let operation = request.operation;
    let mode = request.mode;
    if request.protocol_version != PROTOCOL_VERSION {
        return rejected_response(
            request_id,
            operation,
            mode,
            "protocol_version_mismatch",
            format!(
                "request protocol version does not match kernel version: expected {PROTOCOL_VERSION}"
            ),
            None,
        );
    }

    match operation {
        KernelOperation::ProtocolValidate => match validate_protocol_payload(&request.payload) {
            Ok(protocol_name) => accepted_response(
                request_id,
                KernelOperation::ProtocolValidate,
                mode,
                map_value([
                    ("valid", Value::Bool(true)),
                    ("protocol_name", Value::String(protocol_name)),
                ]),
            ),
            Err((code, message, path)) => rejected_response(
                request_id,
                KernelOperation::ProtocolValidate,
                mode,
                code,
                message,
                path,
            ),
        },
        KernelOperation::HashCanonicalJson => {
            if request.payload.len() != 1 || !request.payload.contains_key("value") {
                return rejected_response(
                    request_id,
                    KernelOperation::HashCanonicalJson,
                    mode,
                    "protocol_invalid",
                    "hash.canonical_json payload must contain exactly one value field".to_owned(),
                    Some("payload"),
                );
            }
            let value = request.payload.get("value").expect("checked above");
            match canonical_json(value) {
                Ok(canonical) => accepted_response(
                    request_id,
                    KernelOperation::HashCanonicalJson,
                    mode,
                    map_value([
                        ("canonical_json", Value::String(canonical.clone())),
                        ("sha256", Value::String(sha256_hex(canonical.as_bytes()))),
                    ]),
                ),
                Err(error) => rejected_response(
                    request_id,
                    KernelOperation::HashCanonicalJson,
                    mode,
                    "protocol_invalid",
                    error.to_string(),
                    Some("payload.value"),
                ),
            }
        }
        KernelOperation::ArtifactVerify => {
            match verify_artifact_payload(&request.payload, project_root) {
                Ok(result) => {
                    accepted_response(request_id, KernelOperation::ArtifactVerify, mode, result)
                }
                Err((code, message, path)) => rejected_response(
                    request_id,
                    KernelOperation::ArtifactVerify,
                    mode,
                    code,
                    message,
                    path,
                ),
            }
        }
        KernelOperation::LedgerVerify => match verify_ledger_payload(&request.payload) {
            Ok(result) => {
                accepted_response(request_id, KernelOperation::LedgerVerify, mode, result)
            }
            Err((code, message, path)) => rejected_response(
                request_id,
                KernelOperation::LedgerVerify,
                mode,
                code,
                message,
                path,
            ),
        },
        KernelOperation::EvidenceClassify => {
            match classify_evidence_payload(&request.payload, project_root, mode) {
                Ok((result, diagnostics)) => accepted_response_with_diagnostics(
                    request_id,
                    KernelOperation::EvidenceClassify,
                    mode,
                    result,
                    diagnostics,
                ),
                Err((code, message, path)) => rejected_response(
                    request_id,
                    KernelOperation::EvidenceClassify,
                    mode,
                    code,
                    message,
                    path,
                ),
            }
        }
        KernelOperation::EvidenceValidateBundle => {
            match validate_evidence_bundle_payload(&request.payload, project_root) {
                Ok(result) => accepted_response(
                    request_id,
                    KernelOperation::EvidenceValidateBundle,
                    mode,
                    result,
                ),
                Err((code, message, path)) => rejected_response(
                    request_id,
                    KernelOperation::EvidenceValidateBundle,
                    mode,
                    code,
                    message,
                    path,
                ),
            }
        }
        KernelOperation::ClaimResolve => {
            match resolve_claim_payload(&request.payload, project_root) {
                Ok((result, diagnostics)) => accepted_response_with_diagnostics(
                    request_id,
                    KernelOperation::ClaimResolve,
                    mode,
                    result,
                    diagnostics,
                ),
                Err((code, message, path)) => rejected_response(
                    request_id,
                    KernelOperation::ClaimResolve,
                    mode,
                    code,
                    message,
                    path,
                ),
            }
        }
        KernelOperation::CheckpointVerify => {
            match verify_checkpoint_payload(&request.payload, project_root) {
                Ok((result, diagnostics)) => accepted_response_with_diagnostics(
                    request_id,
                    KernelOperation::CheckpointVerify,
                    mode,
                    result,
                    diagnostics,
                ),
                Err((code, message, path)) => rejected_response(
                    request_id,
                    KernelOperation::CheckpointVerify,
                    mode,
                    code,
                    message,
                    path,
                ),
            }
        }
        KernelOperation::ReplayVerifyCore => {
            match verify_replay_core_payload(&request.payload, project_root) {
                Ok(result) => {
                    accepted_response(request_id, KernelOperation::ReplayVerifyCore, mode, result)
                }
                Err((code, message, path)) => rejected_response(
                    request_id,
                    KernelOperation::ReplayVerifyCore,
                    mode,
                    code,
                    message,
                    path,
                ),
            }
        }
        KernelOperation::ArtifactPersist => {
            let Some(root) = project_root else {
                return rejected_response(
                    request_id,
                    KernelOperation::ArtifactPersist,
                    mode,
                    "artifact_persist_run_missing",
                    "artifact persistence requires a configured project root".to_owned(),
                    Some("payload.run_id"),
                );
            };
            match persist_artifact_payload(&request.payload, root) {
                Ok((result, diagnostics)) => KernelResponse {
                    protocol_version: PROTOCOL_VERSION,
                    kernel_version: KERNEL_VERSION,
                    request_id,
                    operation: KernelOperation::ArtifactPersist,
                    mode,
                    status: KernelResponseStatus::Accepted,
                    result,
                    diagnostics,
                    mutation_performed: true,
                },
                Err(error) => rejected_or_error_response_with_mutation(
                    request_id,
                    KernelOperation::ArtifactPersist,
                    mode,
                    error.status,
                    error.code,
                    error.message,
                    error.path,
                    error.mutation_performed,
                ),
            }
        }
        KernelOperation::LedgerAppend => {
            let Some(root) = project_root else {
                return rejected_response(
                    request_id,
                    KernelOperation::LedgerAppend,
                    mode,
                    "ledger_append_run_missing",
                    "ledger append requires a configured project root".to_owned(),
                    Some("payload.run_id"),
                );
            };
            match append_ledger_payload(&request.payload, root) {
                Ok(result) => KernelResponse {
                    protocol_version: PROTOCOL_VERSION,
                    kernel_version: KERNEL_VERSION,
                    request_id,
                    operation: KernelOperation::LedgerAppend,
                    mode,
                    status: KernelResponseStatus::Accepted,
                    result,
                    diagnostics: Vec::new(),
                    mutation_performed: true,
                },
                Err(error) => rejected_or_error_response_with_mutation(
                    request_id,
                    KernelOperation::LedgerAppend,
                    mode,
                    error.status,
                    error.code,
                    error.message,
                    error.path,
                    error.mutation_performed,
                ),
            }
        }
        KernelOperation::ArtifactLink => {
            let Some(root) = project_root else {
                return rejected_response(
                    request_id,
                    KernelOperation::ArtifactLink,
                    mode,
                    "artifact_link_run_missing",
                    "artifact linking requires a configured project root".to_owned(),
                    Some("payload.run_id"),
                );
            };
            match link_artifact_payload(&request.payload, root) {
                Ok((result, diagnostics)) => KernelResponse {
                    protocol_version: PROTOCOL_VERSION,
                    kernel_version: KERNEL_VERSION,
                    request_id,
                    operation: KernelOperation::ArtifactLink,
                    mode,
                    status: KernelResponseStatus::Accepted,
                    result,
                    diagnostics,
                    mutation_performed: true,
                },
                Err(error) => rejected_or_error_response_with_mutation(
                    request_id,
                    KernelOperation::ArtifactLink,
                    mode,
                    error.status,
                    error.code,
                    error.message,
                    error.path,
                    error.mutation_performed,
                ),
            }
        }
        KernelOperation::PersistenceCommitBundle => {
            let Some(root) = project_root else {
                return rejected_response(
                    request_id,
                    KernelOperation::PersistenceCommitBundle,
                    mode,
                    "persistence_bundle_run_missing",
                    "persistence bundle requires a configured project root".to_owned(),
                    Some("payload.run_id"),
                );
            };
            match commit_bundle_payload(&request.payload, root) {
                Ok(result) => KernelResponse {
                    protocol_version: PROTOCOL_VERSION,
                    kernel_version: KERNEL_VERSION,
                    request_id,
                    operation: KernelOperation::PersistenceCommitBundle,
                    mode,
                    status: KernelResponseStatus::Accepted,
                    result,
                    diagnostics: Vec::new(),
                    mutation_performed: true,
                },
                Err(error) => rejected_or_error_response_with_mutation(
                    request_id,
                    KernelOperation::PersistenceCommitBundle,
                    mode,
                    error.status,
                    error.code,
                    error.message,
                    error.path,
                    error.mutation_performed,
                ),
            }
        }
    }
}

pub fn parse_and_handle(input: &str) -> Result<KernelResponse, KernelError> {
    parse_and_handle_with_root(input, None)
}

pub fn parse_and_handle_with_root(
    input: &str,
    project_root: Option<PathBuf>,
) -> Result<KernelResponse, KernelError> {
    let value = parse_json_without_duplicate_keys(input)?;
    let request: KernelRequest = serde_json::from_value(value)
        .map_err(|error| KernelError::InvalidRequest(error.to_string()))?;
    validate_transport_request(&request).map_err(KernelError::InvalidRequest)?;
    Ok(handle_request(request, project_root.as_deref()))
}

pub fn transport_error_response(error: &KernelError) -> KernelResponse {
    rejected_or_error_response(
        "transport-error".to_owned(),
        KernelOperation::ProtocolValidate,
        KernelMode::DevelopmentCompatibility,
        KernelResponseStatus::Error,
        "transport_invalid",
        error.to_string(),
        None,
    )
}

fn validate_protocol_payload(
    payload: &Map<String, Value>,
) -> Result<String, (&'static str, String, Option<&'static str>)> {
    if payload.len() != 2
        || !payload.contains_key("protocol_name")
        || !payload.contains_key("instance")
    {
        return Err((
            "protocol_invalid",
            "protocol.validate payload must contain exactly protocol_name and instance fields"
                .to_owned(),
            Some("payload"),
        ));
    }
    let protocol_name = payload
        .get("protocol_name")
        .and_then(Value::as_str)
        .ok_or((
            "protocol_invalid",
            "protocol_name must be a string".to_owned(),
            Some("payload.protocol_name"),
        ))?;
    let instance = payload.get("instance").expect("checked above").clone();
    let result = match protocol_name {
        "KernelRequestEnvelope" => serde_json::from_value::<KernelRequest>(instance.clone())
            .map_err(|error| error.to_string())
            .and_then(|request| validate_kernel_request(&request)),
        "KernelResponseEnvelope" => serde_json::from_value::<WireKernelResponse>(instance)
            .map_err(|error| error.to_string())
            .and_then(|response| validate_kernel_response(&response)),
        _ => {
            return Err((
                "unsupported_protocol",
                format!("unsupported protocol: {protocol_name}"),
                Some("payload.protocol_name"),
            ));
        }
    };
    result
        .map(|_| protocol_name.to_owned())
        .map_err(|error| ("protocol_invalid", error, Some("payload.instance")))
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct WireKernelResponse {
    protocol_version: String,
    kernel_version: String,
    request_id: String,
    operation: KernelOperation,
    mode: KernelMode,
    status: KernelResponseStatus,
    result: Map<String, Value>,
    diagnostics: Vec<KernelDiagnostic>,
    mutation_performed: bool,
}

fn validate_kernel_request(request: &KernelRequest) -> Result<(), String> {
    if request.protocol_version.is_empty() {
        return Err("protocol_version must not be empty".to_owned());
    }
    if request.request_id.is_empty() {
        return Err("request_id must not be empty".to_owned());
    }
    match request.operation {
        KernelOperation::HashCanonicalJson => {
            if request.payload.len() != 1 || !request.payload.contains_key("value") {
                return Err(
                    "hash.canonical_json payload must contain exactly one value field".to_owned(),
                );
            }
        }
        KernelOperation::ProtocolValidate => {
            if request.payload.len() != 2
                || !request.payload.contains_key("protocol_name")
                || !request.payload.contains_key("instance")
            {
                return Err(
                    "protocol.validate payload must contain exactly protocol_name and instance fields"
                        .to_owned(),
                );
            }
            let protocol_name = request
                .payload
                .get("protocol_name")
                .and_then(Value::as_str)
                .ok_or_else(|| "protocol_name must be a string".to_owned())?;
            if !matches!(
                protocol_name,
                "KernelRequestEnvelope" | "KernelResponseEnvelope"
            ) {
                return Err(format!("unsupported protocol: {protocol_name}"));
            }
            if !request
                .payload
                .get("instance")
                .is_some_and(Value::is_object)
            {
                return Err("instance must be an object".to_owned());
            }
        }
        KernelOperation::ArtifactVerify => validate_artifact_payload_structure(&request.payload)?,
        KernelOperation::LedgerVerify => validate_ledger_payload_structure(&request.payload)?,
        KernelOperation::EvidenceClassify => validate_artifact_payload_structure(&request.payload)?,
        KernelOperation::EvidenceValidateBundle => {
            validate_evidence_bundle_payload_structure(&request.payload)
                .map_err(|(_, message, _)| message)?
        }
        KernelOperation::ClaimResolve => {
            serde_json::from_value::<ClaimResolvePayload>(Value::Object(request.payload.clone()))
                .map_err(|error| error.to_string())?;
        }
        KernelOperation::CheckpointVerify => {
            validate_checkpoint_payload_structure(&request.payload)?;
        }
        KernelOperation::ReplayVerifyCore => {
            validate_replay_core_payload_structure(&request.payload)?;
        }
        KernelOperation::ArtifactPersist => {
            // Operation-specific validation is performed in the mutating handler so it can
            // return the frozen artifact_persist_* diagnostic and mutation semantics.
        }
        KernelOperation::LedgerAppend => {
            // Operation-specific validation is performed in the mutating handler so it can
            // return the frozen ledger_append_* diagnostic and mutation semantics.
        }
        KernelOperation::ArtifactLink => {
            // Operation-specific validation is performed in the mutating handler so it can
            // return the frozen artifact_link_* diagnostic and mutation semantics.
        }
        KernelOperation::PersistenceCommitBundle => {
            // Operation-specific validation is performed in the mutating handler so it can
            // return the frozen persistence_bundle_* diagnostic and mutation semantics.
        }
    }
    Ok(())
}

fn validate_artifact_persist_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    let request = serde_json::from_value::<ArtifactPersistPayload>(Value::Object(payload.clone()))
        .map_err(|error| error.to_string())?;
    if !is_safe_segment(&request.run_id) || !is_safe_segment(&request.artifact_id) {
        return Err("artifact persist identifiers must use safe segment syntax".to_owned());
    }
    if request
        .filename_stem_optional
        .as_deref()
        .is_some_and(|value| !is_safe_segment(value))
    {
        return Err("filename_stem_optional must use safe segment syntax".to_owned());
    }
    if artifact_directory(&request.artifact_type).is_none() {
        return Err("artifact_type is not supported".to_owned());
    }
    if request.overwrite_policy != "FailIfExists" {
        return Err("overwrite_policy must be FailIfExists".to_owned());
    }
    if request.metadata.len() > 64 {
        return Err("metadata must contain at most 64 entries".to_owned());
    }
    for key in request.metadata.keys() {
        if key.len() > 128
            || matches!(
                key.as_str(),
                "format" | "producer" | "is_verification_evidence"
            )
        {
            return Err("metadata contains a kernel-controlled key".to_owned());
        }
    }
    if scan_forbidden_authority_values(&Value::Object(request.metadata.clone())) {
        return Err("metadata contains forbidden authority values".to_owned());
    }
    let metadata_json = canonical_json(&Value::Object(request.metadata.clone()))
        .map_err(|error| error.to_string())?;
    if metadata_json.len() > 64 * 1024 {
        return Err("metadata exceeds 64 KiB serialized size".to_owned());
    }
    let canonical = canonical_json(&request.json_value).map_err(|error| error.to_string())?;
    if canonical.len() + 1 > 12 * 1024 * 1024 {
        return Err("serialized JSON payload exceeds 12 MiB".to_owned());
    }
    Ok(())
}

fn validate_artifact_link_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    if payload.len() != 5
        || !payload.contains_key("run_id")
        || !payload.contains_key("expected_ledger_tip_hash")
        || !payload.contains_key("artifact")
        || !payload.contains_key("producing_commit_hash")
        || !payload.contains_key("overwrite_policy")
    {
        return Err("artifact.link payload must contain exactly five fields".to_owned());
    }
    let request = serde_json::from_value::<ArtifactLinkPayload>(Value::Object(payload.clone()))
        .map_err(|error| error.to_string())?;
    if !is_safe_segment(&request.run_id)
        || !is_sha256_hex(&request.expected_ledger_tip_hash)
        || !is_sha256_hex(&request.producing_commit_hash)
        || request.overwrite_policy != "FailIfExists"
    {
        return Err(
            "artifact.link payload contains an invalid identifier, hash, or policy".to_owned(),
        );
    }
    if request.artifact.producing_commit_hash.is_some() {
        return Err("artifact.link requires an unlinked artifact".to_owned());
    }
    if request.artifact.metadata.len() > 64
        || request.artifact.metadata.keys().any(|key| key.len() > 128)
        || scan_forbidden_authority_values(&Value::Object(request.artifact.metadata.clone()))
    {
        return Err("artifact metadata exceeds bounds or contains forbidden authority".to_owned());
    }
    let metadata_json = canonical_json(&Value::Object(request.artifact.metadata.clone()))
        .map_err(|error| error.to_string())?;
    if metadata_json.len() > 64 * 1024 {
        return Err("artifact metadata exceeds 64 KiB serialized size".to_owned());
    }
    validate_artifact_location(&request.run_id, &request.artifact)
}

#[derive(Debug)]
struct LedgerAppendFailure {
    status: KernelResponseStatus,
    code: &'static str,
    message: String,
    path: Option<&'static str>,
    mutation_performed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RawLedgerRow {
    commit_hash: String,
    parent_hash: Option<String>,
    run_id: String,
    candidate_id: Option<String>,
    action_type: String,
    payload_json: String,
    artifact_refs_json: String,
    timestamp: String,
}

fn load_raw_ledger_rows(connection: &Connection) -> Result<Vec<RawLedgerRow>, String> {
    let mut statement = connection
        .prepare(
            "SELECT commit_hash, parent_hash, run_id, candidate_id, action_type, \
             payload_json, artifact_refs_json, timestamp FROM commits ORDER BY rowid",
        )
        .map_err(|_| "persisted ledger schema cannot be queried".to_owned())?;
    let rows = statement
        .query_map([], |row| {
            Ok(RawLedgerRow {
                commit_hash: row.get(0)?,
                parent_hash: row.get(1)?,
                run_id: row.get(2)?,
                candidate_id: row.get(3)?,
                action_type: row.get(4)?,
                payload_json: row.get(5)?,
                artifact_refs_json: row.get(6)?,
                timestamp: row.get(7)?,
            })
        })
        .map_err(|_| "persisted ledger rows cannot be read".to_owned())?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|_| "persisted ledger row has an invalid SQLite type".to_owned())
}

fn raw_rows_to_ledger(run_id: &str, rows: &[RawLedgerRow]) -> Result<LedgerVerifyPayload, String> {
    let mut commits = Vec::with_capacity(rows.len());
    for row in rows {
        let payload_value = parse_json_without_duplicate_keys(&row.payload_json)
            .map_err(|_| "persisted ledger payload JSON is invalid".to_owned())?;
        let payload = payload_value
            .as_object()
            .cloned()
            .ok_or_else(|| "persisted ledger payload must be an object".to_owned())?;
        if canonical_json(&payload_value)
            .map_err(|_| "persisted ledger payload cannot be canonicalized".to_owned())?
            != row.payload_json
        {
            return Err("persisted ledger payload JSON is not canonical".to_owned());
        }
        let artifact_value = parse_json_without_duplicate_keys(&row.artifact_refs_json)
            .map_err(|_| "persisted ledger artifact-reference JSON is invalid".to_owned())?;
        let artifact_refs = artifact_value
            .as_array()
            .cloned()
            .ok_or_else(|| "persisted ledger artifact references must be an array".to_owned())?;
        if canonical_json(&artifact_value).map_err(|_| {
            "persisted ledger artifact references cannot be canonicalized".to_owned()
        })? != row.artifact_refs_json
        {
            return Err("persisted ledger artifact-reference JSON is not canonical".to_owned());
        }
        commits.push(WireLedgerCommit {
            commit_hash: row.commit_hash.clone(),
            parent_hash: row.parent_hash.clone(),
            run_id: row.run_id.clone(),
            candidate_id: row.candidate_id.clone(),
            action_type: row.action_type.clone(),
            payload,
            artifact_refs,
            timestamp: row.timestamp.clone(),
        });
    }
    Ok(LedgerVerifyPayload {
        run_id: run_id.to_owned(),
        commits,
    })
}

fn normalize_sql(sql: &str) -> String {
    sql.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}

fn validate_exact_read_only_ledger(
    connection: &Connection,
    run_id: &str,
) -> Result<(Vec<RawLedgerRow>, LedgerVerifyPayload), String> {
    let schema = connection
        .prepare("PRAGMA table_info(commits)")
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, i64>(3)?,
                        row.get::<_, Option<String>>(4)?,
                        row.get::<_, i64>(5)?,
                    ))
                })?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(|_| "ledger table schema cannot be inspected".to_owned())?;
    let expected = [
        (0, "commit_hash", "TEXT", 0, None, 1),
        (1, "parent_hash", "TEXT", 0, None, 0),
        (2, "run_id", "TEXT", 1, None, 0),
        (3, "candidate_id", "TEXT", 0, None, 0),
        (4, "action_type", "TEXT", 1, None, 0),
        (5, "payload_json", "TEXT", 1, None, 0),
        (6, "artifact_refs_json", "TEXT", 1, None, 0),
        (7, "timestamp", "TEXT", 1, None, 0),
    ];
    if schema.len() != expected.len()
        || !schema.iter().zip(expected.iter()).all(|(got, want)| {
            got.0 == want.0
                && got.1 == want.1
                && got.2 == want.2
                && got.3 == want.3
                && got.4.as_deref() == want.4
                && got.5 == want.5
        })
    {
        return Err("ledger schema is not the supported append-only schema".to_owned());
    }
    let table_sql: Option<String> = connection
        .query_row(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='commits'",
            [],
            |row| row.get(0),
        )
        .map_err(|_| "ledger table definition cannot be inspected".to_owned())?;
    let expected_table_sql = "create table commits ( commit_hash text primary key, parent_hash text references commits(commit_hash), run_id text not null, candidate_id text, action_type text not null, payload_json text not null, artifact_refs_json text not null, timestamp text not null )";
    if table_sql
        .as_deref()
        .is_none_or(|sql| normalize_sql(sql) != expected_table_sql)
    {
        return Err("ledger table definition is invalid".to_owned());
    }
    let foreign_keys = connection
        .prepare("PRAGMA foreign_key_list(commits)")
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, String>(6)?,
                        row.get::<_, String>(7)?,
                    ))
                })?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(|_| "ledger foreign-key schema cannot be inspected".to_owned())?;
    let expected_foreign_key = vec![(
        0,
        0,
        "commits".to_owned(),
        "parent_hash".to_owned(),
        "commit_hash".to_owned(),
        "NO ACTION".to_owned(),
        "NO ACTION".to_owned(),
        "NONE".to_owned(),
    )];
    if foreign_keys != expected_foreign_key {
        return Err("ledger parent foreign-key schema is invalid".to_owned());
    }
    let trigger_rows = connection
        .prepare("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='commits'")
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
                })?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(|_| "ledger trigger schema cannot be inspected".to_owned())?;
    let expected_triggers = [
        (
            "commits_no_delete",
            "create trigger commits_no_delete before delete on commits begin select raise(abort, 'commits are append-only'); end",
        ),
        (
            "commits_no_update",
            "create trigger commits_no_update before update on commits begin select raise(abort, 'commits are append-only'); end",
        ),
    ];
    if trigger_rows.len() != expected_triggers.len()
        || expected_triggers
            .iter()
            .any(|(expected_name, expected_sql)| {
                !trigger_rows.iter().any(|(name, sql)| {
                    name == expected_name
                        && sql
                            .as_deref()
                            .is_some_and(|value| normalize_sql(value) == *expected_sql)
                })
            })
    {
        return Err("ledger append-only trigger schema is invalid".to_owned());
    }
    let integrity: String = connection
        .query_row("PRAGMA integrity_check", [], |row| row.get(0))
        .map_err(|_| "ledger integrity check could not run".to_owned())?;
    if integrity != "ok" {
        return Err("ledger integrity check failed".to_owned());
    }
    let foreign_key_findings = connection
        .prepare("PRAGMA foreign_key_check")
        .and_then(|mut statement| {
            statement
                .query_map([], |_| Ok(()))?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(|_| "ledger foreign-key check could not run".to_owned())?;
    if !foreign_key_findings.is_empty() {
        return Err("ledger foreign-key check failed".to_owned());
    }
    let rows = load_raw_ledger_rows(connection)?;
    if rows.is_empty() {
        return Err("ledger is empty".to_owned());
    }
    let ledger = raw_rows_to_ledger(run_id, &rows)?;
    verify_ledger(&ledger).map_err(|(_, message, _)| message)?;
    Ok((rows, ledger))
}

fn ledger_append_failure(
    code: &'static str,
    message: impl Into<String>,
    path: Option<&'static str>,
) -> LedgerAppendFailure {
    LedgerAppendFailure {
        status: KernelResponseStatus::Rejected,
        code,
        message: message.into(),
        path,
        mutation_performed: false,
    }
}

fn validate_utc_timestamp(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 20
        || bytes.len() > 27
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || *bytes.last().unwrap_or(&0) != b'Z'
    {
        return false;
    }
    let digit = |b: u8| b.is_ascii_digit();
    for &idx in &[0usize, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18] {
        if !digit(bytes[idx]) {
            return false;
        }
    }
    if bytes.len() > 20 {
        let fraction = &bytes[20..bytes.len() - 1];
        if bytes[19] != b'.'
            || fraction.is_empty()
            || fraction.len() > 6
            || !fraction.iter().all(|b| digit(*b))
        {
            return false;
        }
    }
    let num =
        |start: usize, len: usize| -> u32 { value[start..start + len].parse().unwrap_or(999) };
    let year = num(0, 4);
    let month = num(5, 2);
    let day = num(8, 2);
    let hour = num(11, 2);
    let minute = num(14, 2);
    let second = num(17, 2);
    if year == 0 || !(1..=12).contains(&month) || hour > 23 || minute > 59 || second > 59 {
        return false;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    (1..=days[(month - 1) as usize]).contains(&day)
}

fn append_ledger_payload(
    payload: &Map<String, Value>,
    project_root: &Path,
) -> Result<Map<String, Value>, LedgerAppendFailure> {
    append_ledger_payload_with_fault(payload, project_root, LedgerAppendFault::None)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
enum LedgerAppendFault {
    None,
    Open,
    Begin,
    Validate,
    Insert,
    Readback,
    Rollback,
    Commit,
    Reopen,
    Postcondition,
}

fn append_ledger_payload_with_fault(
    payload: &Map<String, Value>,
    project_root: &Path,
    fault: LedgerAppendFault,
) -> Result<Map<String, Value>, LedgerAppendFailure> {
    let request = serde_json::from_value::<LedgerAppendPayload>(Value::Object(payload.clone()))
        .map_err(|error| {
            ledger_append_failure(
                "ledger_append_payload_invalid",
                error.to_string(),
                Some("payload"),
            )
        })?;
    if !is_safe_segment(&request.run_id)
        || !is_sha256_hex(&request.expected_tip_hash)
        || request.action_type == "InitRun"
        || !is_valid_action_type(&request.action_type)
        || request
            .candidate_id_optional
            .as_deref()
            .is_some_and(|v| !is_safe_segment(v))
    {
        return Err(ledger_append_failure(
            "ledger_append_payload_invalid",
            "invalid run, tip, action, or candidate identifier",
            Some("payload"),
        ));
    }
    if !validate_utc_timestamp(&request.timestamp) {
        return Err(ledger_append_failure(
            "ledger_append_payload_invalid",
            "timestamp must be a real ASCII UTC timestamp",
            Some("payload.timestamp"),
        ));
    }
    let payload_json = canonical_json(&Value::Object(request.payload.clone())).map_err(|e| {
        ledger_append_failure(
            "ledger_append_payload_invalid",
            e.to_string(),
            Some("payload.payload"),
        )
    })?;
    if payload_json.len() > 4 * 1024 * 1024 {
        return Err(ledger_append_failure(
            "ledger_append_size_exceeded",
            "serialized payload exceeds 4 MiB",
            Some("payload.payload"),
        ));
    }
    let root_meta = fs::symlink_metadata(project_root).map_err(|_| {
        ledger_append_failure(
            "ledger_append_run_missing",
            "project root does not exist",
            Some("payload.run_id"),
        )
    })?;
    if !root_meta.is_dir() || root_meta.file_type().is_symlink() {
        return Err(ledger_append_failure(
            "ledger_append_root_unsupported",
            "project root must be a real directory",
            Some("payload.run_id"),
        ));
    }
    let runs = project_root.join("runs");
    let run = runs.join(&request.run_id);
    for path in [&runs, &run] {
        let meta = fs::symlink_metadata(path).map_err(|_| {
            ledger_append_failure(
                "ledger_append_run_missing",
                "run directory is missing",
                Some("payload.run_id"),
            )
        })?;
        if !meta.is_dir() || meta.file_type().is_symlink() {
            return Err(ledger_append_failure(
                "ledger_append_directory_invalid",
                "run directory must be a real directory",
                Some("payload.run_id"),
            ));
        }
    }
    let ledger_path = run.join("ledger.sqlite");
    let ledger_meta = fs::symlink_metadata(&ledger_path).map_err(|_| {
        ledger_append_failure(
            "ledger_append_ledger_invalid",
            "ledger.sqlite is missing",
            Some("payload.run_id"),
        )
    })?;
    if !ledger_meta.is_file() || ledger_meta.file_type().is_symlink() {
        return Err(ledger_append_failure(
            "ledger_append_ledger_invalid",
            "ledger.sqlite must be a regular file",
            Some("payload.run_id"),
        ));
    }
    for suffix in ["-journal", "-wal", "-shm"] {
        if fs::symlink_metadata(run.join(format!("ledger.sqlite{suffix}"))).is_ok() {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger auxiliary files are not allowed",
                Some("payload.run_id"),
            ));
        }
    }
    if fault == LedgerAppendFault::Open {
        return Err(ledger_append_failure(
            "ledger_append_ledger_invalid",
            "ledger open fault",
            Some("payload.run_id"),
        ));
    }
    let flags = OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let conn = Connection::open_with_flags(&ledger_path, flags).map_err(|e| {
        ledger_append_failure(
            "ledger_append_ledger_invalid",
            format!("ledger cannot be opened: {:?}", e.sqlite_error_code()),
            Some("payload.run_id"),
        )
    })?;
    let journal_mode_before: String = conn
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .map_err(|_| {
            ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger journal mode cannot be read",
                Some("payload.run_id"),
            )
        })?;
    conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA busy_timeout=0; PRAGMA synchronous=FULL;")
        .map_err(|_| {
            ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger connection safety settings could not be applied",
                Some("payload.run_id"),
            )
        })?;
    let connection_settings = (
        conn.query_row("PRAGMA foreign_keys", [], |row| row.get::<_, i64>(0)),
        conn.query_row("PRAGMA busy_timeout", [], |row| row.get::<_, i64>(0)),
        conn.query_row("PRAGMA synchronous", [], |row| row.get::<_, i64>(0)),
    );
    if !matches!(connection_settings, (Ok(1), Ok(0), Ok(2 | 3))) {
        return Err(ledger_append_failure(
            "ledger_append_ledger_invalid",
            "ledger connection safety settings are not active",
            Some("payload.run_id"),
        ));
    }
    if fault == LedgerAppendFault::Begin {
        return Err(ledger_append_failure(
            "ledger_append_busy",
            "ledger begin fault",
            Some("payload.expected_tip_hash"),
        ));
    }
    match conn.execute_batch("BEGIN IMMEDIATE") {
        Ok(()) => {}
        Err(rusqlite::Error::SqliteFailure(ref error, _))
            if matches!(
                error.code,
                rusqlite::ffi::ErrorCode::DatabaseBusy | rusqlite::ffi::ErrorCode::DatabaseLocked
            ) =>
        {
            return Err(ledger_append_failure(
                "ledger_append_busy",
                "ledger is busy",
                Some("payload.expected_tip_hash"),
            ))
        }
        Err(_) => {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger immediate transaction could not begin",
                Some("payload.run_id"),
            ))
        }
    }
    let result = (|| {
        if fault == LedgerAppendFault::Validate {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger validation fault",
                Some("payload.run_id"),
            ));
        }
        let schema = conn
            .prepare("PRAGMA table_info(commits)")
            .and_then(|mut s| {
                s.query_map([], |r| {
                    Ok((
                        r.get::<_, i64>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                        r.get::<_, i64>(3)?,
                        r.get::<_, Option<String>>(4)?,
                        r.get::<_, i64>(5)?,
                    ))
                })
                .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
            })
            .map_err(|e| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    e.to_string(),
                    Some("payload.run_id"),
                )
            })?;
        let expected = [
            (0, "commit_hash", "TEXT", 0, None, 1),
            (1, "parent_hash", "TEXT", 0, None, 0),
            (2, "run_id", "TEXT", 1, None, 0),
            (3, "candidate_id", "TEXT", 0, None, 0),
            (4, "action_type", "TEXT", 1, None, 0),
            (5, "payload_json", "TEXT", 1, None, 0),
            (6, "artifact_refs_json", "TEXT", 1, None, 0),
            (7, "timestamp", "TEXT", 1, None, 0),
        ];
        let schema_ok = schema.len() == expected.len()
            && schema.iter().zip(expected.iter()).all(|(got, want)| {
                got.0 == want.0
                    && got.1 == want.1
                    && got.2 == want.2
                    && got.3 == want.3
                    && got.4 == want.4
                    && got.5 == want.5
            });
        if !schema_ok {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger schema is not the supported append-only schema",
                Some("payload.run_id"),
            ));
        }
        let table_sql: Option<String> = conn
            .query_row(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='commits'",
                [],
                |row| row.get(0),
            )
            .map_err(|_| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    "ledger table definition cannot be inspected",
                    Some("payload.run_id"),
                )
            })?;
        let expected_table_sql = "create table commits ( commit_hash text primary key, parent_hash text references commits(commit_hash), run_id text not null, candidate_id text, action_type text not null, payload_json text not null, artifact_refs_json text not null, timestamp text not null )";
        if table_sql
            .as_deref()
            .is_none_or(|sql| normalize_sql(sql) != expected_table_sql)
        {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger table definition is invalid",
                Some("payload.run_id"),
            ));
        }
        let foreign_keys = conn
            .prepare("PRAGMA foreign_key_list(commits)")
            .and_then(|mut statement| {
                statement
                    .query_map([], |row| {
                        Ok((
                            row.get::<_, i64>(0)?,
                            row.get::<_, i64>(1)?,
                            row.get::<_, String>(2)?,
                            row.get::<_, String>(3)?,
                            row.get::<_, String>(4)?,
                            row.get::<_, String>(5)?,
                            row.get::<_, String>(6)?,
                            row.get::<_, String>(7)?,
                        ))
                    })?
                    .collect::<Result<Vec<_>, _>>()
            })
            .map_err(|_| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    "ledger foreign-key schema cannot be inspected",
                    Some("payload.run_id"),
                )
            })?;
        let expected_foreign_key = vec![(
            0,
            0,
            "commits".to_owned(),
            "parent_hash".to_owned(),
            "commit_hash".to_owned(),
            "NO ACTION".to_owned(),
            "NO ACTION".to_owned(),
            "NONE".to_owned(),
        )];
        if foreign_keys != expected_foreign_key {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger parent foreign-key schema is invalid",
                Some("payload.run_id"),
            ));
        }
        let mut triggers = conn
            .prepare(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='commits'",
            )
            .map_err(|e| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    e.to_string(),
                    Some("payload.run_id"),
                )
            })?;
        let trigger_rows = triggers
            .query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
            })
            .map_err(|e| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    e.to_string(),
                    Some("payload.run_id"),
                )
            })?;
        let trigger_rows = trigger_rows.collect::<Result<Vec<_>, _>>().map_err(|e| {
            ledger_append_failure(
                "ledger_append_ledger_invalid",
                e.to_string(),
                Some("payload.run_id"),
            )
        })?;
        let expected_triggers = [
            (
                "commits_no_delete",
                "create trigger commits_no_delete before delete on commits begin select raise(abort, 'commits are append-only'); end",
            ),
            (
                "commits_no_update",
                "create trigger commits_no_update before update on commits begin select raise(abort, 'commits are append-only'); end",
            ),
        ];
        if trigger_rows.len() != expected_triggers.len()
            || expected_triggers
                .iter()
                .any(|(expected_name, expected_sql)| {
                    !trigger_rows.iter().any(|(name, sql)| {
                        name == expected_name
                            && sql
                                .as_deref()
                                .is_some_and(|value| normalize_sql(value) == *expected_sql)
                    })
                })
        {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger append-only trigger schema is invalid",
                Some("payload.run_id"),
            ));
        }
        let integrity: String = conn
            .query_row("PRAGMA integrity_check", [], |r| r.get(0))
            .map_err(|e| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    e.to_string(),
                    Some("payload.run_id"),
                )
            })?;
        if integrity != "ok" {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger integrity check failed",
                Some("payload.run_id"),
            ));
        }
        let mut fk = conn.prepare("PRAGMA foreign_key_check").map_err(|e| {
            ledger_append_failure(
                "ledger_append_ledger_invalid",
                e.to_string(),
                Some("payload.run_id"),
            )
        })?;
        let mut fk_rows = fk.query([]).map_err(|e| {
            ledger_append_failure(
                "ledger_append_ledger_invalid",
                e.to_string(),
                Some("payload.run_id"),
            )
        })?;
        if fk_rows
            .next()
            .map_err(|e| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    e.to_string(),
                    Some("payload.run_id"),
                )
            })?
            .is_some()
        {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger foreign-key check failed",
                Some("payload.run_id"),
            ));
        }
        let raw_rows = load_raw_ledger_rows(&conn).map_err(|message| {
            ledger_append_failure(
                "ledger_append_ledger_invalid",
                message,
                Some("payload.run_id"),
            )
        })?;
        let commits = raw_rows_to_ledger(&request.run_id, &raw_rows)
            .map_err(|message| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    message,
                    Some("payload.run_id"),
                )
            })?
            .commits;
        if commits.is_empty() || commits.iter().any(|c| c.run_id != request.run_id) {
            return Err(ledger_append_failure(
                "ledger_append_ledger_invalid",
                "ledger must contain one non-empty history for this run",
                Some("payload.run_id"),
            ));
        }
        let ledger = LedgerVerifyPayload {
            run_id: request.run_id.clone(),
            commits,
        };
        verify_ledger(&ledger).map_err(|(_, m, _)| {
            ledger_append_failure("ledger_append_ledger_invalid", m, Some("payload.run_id"))
        })?;
        let previous = ledger
            .commits
            .last()
            .map(|c| c.commit_hash.clone())
            .ok_or_else(|| {
                ledger_append_failure(
                    "ledger_append_ledger_invalid",
                    "ledger is empty",
                    Some("payload.run_id"),
                )
            })?;
        if previous != request.expected_tip_hash {
            return Err(ledger_append_failure(
                "ledger_append_tip_mismatch",
                "expected tip does not match current ledger tip",
                Some("payload.expected_tip_hash"),
            ));
        }
        let candidate = WireLedgerCommit {
            commit_hash: String::new(),
            parent_hash: Some(previous.clone()),
            run_id: request.run_id.clone(),
            candidate_id: request.candidate_id_optional.clone(),
            action_type: request.action_type.clone(),
            payload: request.payload.clone(),
            artifact_refs: Vec::new(),
            timestamp: request.timestamp.clone(),
        };
        let new_hash = compute_wire_commit_hash(&candidate).map_err(|e| {
            ledger_append_failure("ledger_append_insert_failed", e, Some("payload"))
        })?;
        if fault == LedgerAppendFault::Insert {
            return Err(ledger_append_failure(
                "ledger_append_insert_failed",
                "ledger insert fault",
                Some("payload"),
            ));
        }
        conn.execute("INSERT INTO commits (commit_hash,parent_hash,run_id,candidate_id,action_type,payload_json,artifact_refs_json,timestamp) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)", params![new_hash, previous, request.run_id, request.candidate_id_optional, request.action_type, payload_json, "[]", request.timestamp]).map_err(|_| ledger_append_failure("ledger_append_insert_failed", "ledger insert failed", Some("payload")))?;
        if matches!(
            fault,
            LedgerAppendFault::Readback | LedgerAppendFault::Rollback
        ) {
            return Err(ledger_append_failure(
                "ledger_append_insert_failed",
                if fault == LedgerAppendFault::Readback {
                    "ledger readback fault"
                } else {
                    "ledger rollback fault"
                },
                Some("payload"),
            ));
        }
        let row: (String, Option<String>, String, Option<String>, String, String, String, String) = conn.query_row("SELECT commit_hash,parent_hash,run_id,candidate_id,action_type,payload_json,artifact_refs_json,timestamp FROM commits WHERE commit_hash=?1", [&new_hash], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?))).map_err(|e| ledger_append_failure("ledger_append_insert_failed", e.to_string(), Some("payload")))?;
        if row
            != (
                new_hash.clone(),
                Some(previous.clone()),
                request.run_id.clone(),
                request.candidate_id_optional.clone(),
                request.action_type.clone(),
                payload_json.clone(),
                "[]".to_owned(),
                request.timestamp.clone(),
            )
        {
            return Err(ledger_append_failure(
                "ledger_append_insert_failed",
                "inserted ledger row did not round-trip exactly",
                Some("payload"),
            ));
        }
        Ok((ledger, raw_rows, new_hash))
    })();
    let (old, old_raw_rows, new_hash) = match result {
        Ok(v) => v,
        Err(error) => {
            let _ = conn.execute_batch("ROLLBACK");
            return Err(error);
        }
    };
    conn.execute_batch("COMMIT")
        .map_err(|e| LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_commit_uncertain",
            message: e.to_string(),
            path: Some("payload"),
            mutation_performed: true,
        })?;
    if fault == LedgerAppendFault::Commit {
        return Err(LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_commit_uncertain",
            message: "ledger commit outcome is intentionally uncertain".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    drop(conn);
    for suffix in ["-journal", "-wal", "-shm"] {
        if fs::symlink_metadata(run.join(format!("ledger.sqlite{suffix}"))).is_ok() {
            return Err(LedgerAppendFailure {
                status: KernelResponseStatus::Error,
                code: "ledger_append_postcondition_failed",
                message: "ledger auxiliary file remained after commit".to_owned(),
                path: Some("payload.run_id"),
                mutation_performed: true,
            });
        }
    }
    if fault == LedgerAppendFault::Reopen {
        return Err(LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message: "ledger reopen fault".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    let reopened_connection = Connection::open_with_flags(
        &ledger_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|_| LedgerAppendFailure {
        status: KernelResponseStatus::Error,
        code: "ledger_append_postcondition_failed",
        message: "ledger could not be reopened read-only".to_owned(),
        path: Some("payload.run_id"),
        mutation_performed: true,
    })?;
    let journal_mode_after: String = reopened_connection
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .map_err(|_| LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message: "ledger journal mode cannot be rechecked".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        })?;
    if journal_mode_after != journal_mode_before {
        return Err(LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message: "ledger journal mode changed".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    let reopened_raw_rows =
        load_raw_ledger_rows(&reopened_connection).map_err(|message| LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message,
            path: Some("payload.run_id"),
            mutation_performed: true,
        })?;
    let reopened = raw_rows_to_ledger(&request.run_id, &reopened_raw_rows).map_err(|message| {
        LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message,
            path: Some("payload.run_id"),
            mutation_performed: true,
        }
    })?;
    verify_ledger(&reopened).map_err(|(_, message, _)| LedgerAppendFailure {
        status: KernelResponseStatus::Error,
        code: "ledger_append_postcondition_failed",
        message,
        path: Some("payload.run_id"),
        mutation_performed: true,
    })?;
    let expected_new_row = RawLedgerRow {
        commit_hash: new_hash.clone(),
        parent_hash: Some(request.expected_tip_hash.clone()),
        run_id: request.run_id.clone(),
        candidate_id: request.candidate_id_optional.clone(),
        action_type: request.action_type.clone(),
        payload_json: payload_json.clone(),
        artifact_refs_json: "[]".to_owned(),
        timestamp: request.timestamp.clone(),
    };
    if reopened_raw_rows.len() != old_raw_rows.len() + 1
        || reopened_raw_rows[..old_raw_rows.len()] != old_raw_rows
        || reopened_raw_rows.last() != Some(&expected_new_row)
        || reopened.commits.last().map(|c| c.commit_hash.as_str()) != Some(new_hash.as_str())
    {
        return Err(LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message: "ledger prefix postcondition failed".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    if fault == LedgerAppendFault::Postcondition {
        return Err(LedgerAppendFailure {
            status: KernelResponseStatus::Error,
            code: "ledger_append_postcondition_failed",
            message: "ledger postcondition fault".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    let commit = map_value([
        ("commit_hash", Value::String(new_hash.clone())),
        (
            "parent_hash",
            Value::String(old.commits.last().unwrap().commit_hash.clone()),
        ),
        ("run_id", Value::String(request.run_id.clone())),
        (
            "candidate_id",
            request
                .candidate_id_optional
                .clone()
                .map_or(Value::Null, Value::String),
        ),
        ("action_type", Value::String(request.action_type.clone())),
        ("payload", Value::Object(request.payload.clone())),
        ("artifact_refs", Value::Array(Vec::new())),
        ("timestamp", Value::String(request.timestamp.clone())),
    ]);
    Ok(map_value([
        ("commit", Value::Object(commit)),
        (
            "previous_tip_hash",
            Value::String(old.commits.last().unwrap().commit_hash.clone()),
        ),
        ("new_tip_hash", Value::String(new_hash)),
        (
            "commit_count_before",
            Value::Number((old.commits.len() as u64).into()),
        ),
        (
            "commit_count_after",
            Value::Number(((old.commits.len() + 1) as u64).into()),
        ),
        ("appended", Value::Bool(true)),
        ("linked_artifact_count", Value::Number(0.into())),
        ("authority_granted", Value::Bool(false)),
    ]))
}

#[derive(Debug)]
struct ArtifactPersistFailure {
    status: KernelResponseStatus,
    code: &'static str,
    message: String,
    path: Option<&'static str>,
    mutation_performed: bool,
}

#[derive(Debug)]
struct ArtifactLinkFailure {
    status: KernelResponseStatus,
    code: &'static str,
    message: String,
    path: Option<&'static str>,
    mutation_performed: bool,
}

#[derive(Debug)]
struct PersistenceBundleFailure {
    status: KernelResponseStatus,
    code: &'static str,
    message: String,
    path: Option<&'static str>,
    mutation_performed: bool,
}

fn persistence_bundle_failure(
    code: &'static str,
    message: impl Into<String>,
    path: Option<&'static str>,
) -> PersistenceBundleFailure {
    PersistenceBundleFailure {
        status: KernelResponseStatus::Rejected,
        code,
        message: message.into(),
        path,
        mutation_performed: false,
    }
}

fn artifact_link_failure(
    code: &'static str,
    message: impl Into<String>,
    path: Option<&'static str>,
) -> ArtifactLinkFailure {
    ArtifactLinkFailure {
        status: KernelResponseStatus::Rejected,
        code,
        message: message.into(),
        path,
        mutation_performed: false,
    }
}

fn link_artifact_payload(
    payload: &Map<String, Value>,
    project_root: &Path,
) -> Result<(Map<String, Value>, Vec<KernelDiagnostic>), ArtifactLinkFailure> {
    link_artifact_payload_with_fault(payload, project_root, ArtifactLinkFault::None)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
enum ArtifactLinkFault {
    None,
    TempCreate,
    TempWrite,
    TempFlush,
    TempFsync,
    SnapshotBeforePublish,
    Publish,
    TempCleanup,
    DirectoryFsync,
    FinalRead,
    SnapshotAfterPublish,
}

fn link_artifact_payload_with_fault(
    payload: &Map<String, Value>,
    project_root: &Path,
    fault: ArtifactLinkFault,
) -> Result<(Map<String, Value>, Vec<KernelDiagnostic>), ArtifactLinkFailure> {
    let request = serde_json::from_value::<ArtifactLinkPayload>(Value::Object(payload.clone()))
        .map_err(|error| {
            artifact_link_failure(
                "artifact_link_payload_invalid",
                error.to_string(),
                Some("payload"),
            )
        })?;
    validate_artifact_link_payload_structure(payload).map_err(|message| {
        let code = if message.contains("64 KiB") || message.contains("1 MiB") {
            "artifact_link_size_exceeded"
        } else {
            "artifact_link_payload_invalid"
        };
        artifact_link_failure(code, message, Some("payload"))
    })?;
    let root = fs::canonicalize(project_root).map_err(|_| {
        artifact_link_failure(
            "artifact_link_run_missing",
            "project root is unavailable",
            Some("payload.run_id"),
        )
    })?;
    let run_dir = root.join("runs").join(&request.run_id);
    let run_meta = fs::symlink_metadata(&run_dir).map_err(|_| {
        artifact_link_failure(
            "artifact_link_run_missing",
            "run directory is missing",
            Some("payload.run_id"),
        )
    })?;
    if !run_meta.is_dir() || run_meta.file_type().is_symlink() {
        return Err(artifact_link_failure(
            "artifact_link_directory_invalid",
            "run directory is not a real directory",
            Some("payload.run_id"),
        ));
    }
    let artifact_path =
        resolve_run_file(&root, &request.run_id, &request.artifact.path).map_err(|message| {
            artifact_link_failure(
                "artifact_link_artifact_invalid",
                message,
                Some("payload.artifact.path"),
            )
        })?;
    let artifact_bytes_before = fs::read(&artifact_path).map_err(|_| {
        artifact_link_failure(
            "artifact_link_artifact_invalid",
            "artifact bytes cannot be read",
            Some("payload.artifact.content_hash"),
        )
    })?;
    if sha256_hex(&artifact_bytes_before) != request.artifact.content_hash {
        return Err(artifact_link_failure(
            "artifact_link_artifact_invalid",
            "artifact content hash does not match",
            Some("payload.artifact.content_hash"),
        ));
    }
    let ledger_path = run_dir.join("ledger.sqlite");
    let ledger_meta = fs::symlink_metadata(&ledger_path).map_err(|_| {
        artifact_link_failure(
            "artifact_link_ledger_invalid",
            "ledger is missing",
            Some("payload"),
        )
    })?;
    if !ledger_meta.is_file() || ledger_meta.file_type().is_symlink() {
        return Err(artifact_link_failure(
            "artifact_link_ledger_invalid",
            "ledger is not a regular file",
            Some("payload"),
        ));
    }
    for suffix in ["-journal", "-wal", "-shm"] {
        if fs::symlink_metadata(ledger_path.with_file_name(format!("ledger.sqlite{suffix}")))
            .is_ok()
        {
            return Err(artifact_link_failure(
                "artifact_link_ledger_invalid",
                "ledger auxiliary file exists",
                Some("payload"),
            ));
        }
    }
    let ledger_bytes_before = fs::read(&ledger_path).map_err(|_| {
        artifact_link_failure(
            "artifact_link_ledger_invalid",
            "ledger bytes cannot be read",
            Some("payload"),
        )
    })?;
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let connection = Connection::open_with_flags(&ledger_path, flags).map_err(|_| {
        artifact_link_failure(
            "artifact_link_ledger_invalid",
            "ledger cannot be opened read-only",
            Some("payload"),
        )
    })?;
    connection
        .busy_timeout(std::time::Duration::ZERO)
        .map_err(|_| {
            artifact_link_failure(
                "artifact_link_ledger_invalid",
                "ledger busy policy cannot be configured",
                Some("payload"),
            )
        })?;
    match connection.query_row("SELECT 1 FROM commits LIMIT 1", [], |_| Ok(())) {
        Ok(()) | Err(rusqlite::Error::QueryReturnedNoRows) => {}
        Err(rusqlite::Error::SqliteFailure(ref error, _))
            if matches!(
                error.code,
                rusqlite::ffi::ErrorCode::DatabaseBusy | rusqlite::ffi::ErrorCode::DatabaseLocked
            ) =>
        {
            return Err(artifact_link_failure(
                "artifact_link_busy",
                "ledger is busy",
                Some("payload.expected_ledger_tip_hash"),
            ))
        }
        Err(_) => {
            return Err(artifact_link_failure(
                "artifact_link_ledger_invalid",
                "ledger cannot be read",
                Some("payload"),
            ))
        }
    }
    let (ledger_rows_before, ledger) =
        validate_exact_read_only_ledger(&connection, &request.run_id).map_err(|message| {
            artifact_link_failure("artifact_link_ledger_invalid", message, Some("payload"))
        })?;
    drop(connection);
    if fs::read(&ledger_path).ok().as_deref() != Some(ledger_bytes_before.as_slice()) {
        return Err(artifact_link_failure(
            "artifact_link_snapshot_changed",
            "ledger changed during validation",
            Some("payload"),
        ));
    }
    let tip = ledger
        .commits
        .last()
        .map(|commit| commit.commit_hash.as_str())
        .ok_or_else(|| {
            artifact_link_failure(
                "artifact_link_ledger_invalid",
                "ledger is empty",
                Some("payload"),
            )
        })?;
    if tip != request.expected_ledger_tip_hash {
        return Err(artifact_link_failure(
            "artifact_link_tip_mismatch",
            "expected ledger tip does not match",
            Some("payload.expected_ledger_tip_hash"),
        ));
    }
    let producer = ledger
        .commits
        .iter()
        .find(|commit| commit.commit_hash == request.producing_commit_hash)
        .ok_or_else(|| {
            artifact_link_failure(
                "artifact_link_commit_missing",
                "producer commit is not in the ledger",
                Some("payload.producing_commit_hash"),
            )
        })?;
    let matching = producer
        .artifact_refs
        .iter()
        .filter(|value| {
            value.as_object().is_some_and(|object| {
                object.get("id") == Some(&Value::String(request.artifact.id.clone()))
                    && object.get("type")
                        == Some(&Value::String(request.artifact.artifact_type.clone()))
                    && object.get("path") == Some(&Value::String(request.artifact.path.clone()))
                    && object.get("content_hash")
                        == Some(&Value::String(request.artifact.content_hash.clone()))
                    && object.get("metadata")
                        == Some(&Value::Object(request.artifact.metadata.clone()))
                    && object.get("producing_commit_hash")
                        == Some(&Value::String(producer.commit_hash.clone()))
            })
        })
        .count();
    if matching == 0 {
        return Err(artifact_link_failure(
            "artifact_link_commit_mismatch",
            "producer commit does not contain the exact artifact reference",
            Some("payload.artifact"),
        ));
    }
    if matching != 1 {
        return Err(artifact_link_failure(
            "artifact_link_commit_mismatch",
            "producer commit contains duplicate artifact references",
            Some("payload.artifact"),
        ));
    }
    let target = root.join(format!("{}.meta.json", request.artifact.path));
    if fs::symlink_metadata(&target).is_ok() {
        return Err(artifact_link_failure(
            "artifact_link_target_exists",
            "artifact sidecar already exists",
            Some("payload.artifact.path"),
        ));
    }
    let mut linked = serde_json::to_value(&request.artifact).map_err(|error| {
        artifact_link_failure(
            "artifact_link_payload_invalid",
            error.to_string(),
            Some("payload.artifact"),
        )
    })?;
    linked
        .as_object_mut()
        .expect("artifact ref is an object")
        .insert(
            "producing_commit_hash".to_owned(),
            Value::String(request.producing_commit_hash.clone()),
        );
    let canonical = canonical_json(&linked).map_err(|error| {
        artifact_link_failure(
            "artifact_link_payload_invalid",
            error.to_string(),
            Some("payload.artifact"),
        )
    })?;
    let bytes = format!("{canonical}\n").into_bytes();
    if bytes.len() > 1024 * 1024 {
        return Err(artifact_link_failure(
            "artifact_link_size_exceeded",
            "serialized sidecar exceeds 1 MiB",
            Some("payload.artifact"),
        ));
    }
    let parent = target.parent().ok_or_else(|| {
        artifact_link_failure(
            "artifact_link_directory_invalid",
            "sidecar parent is missing",
            Some("payload.artifact.path"),
        )
    })?;
    static LINK_COUNTER: AtomicU64 = AtomicU64::new(0);
    let nonce = LINK_COUNTER.fetch_add(1, Ordering::Relaxed);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let temp = parent.join(format!(
        ".{}.tmp-link-{}-{}",
        target
            .file_name()
            .and_then(|v| v.to_str())
            .unwrap_or("artifact"),
        std::process::id(),
        now + nonce as u128
    ));
    if fault == ArtifactLinkFault::TempCreate {
        return Err(artifact_link_failure(
            "artifact_link_temp_write_failed",
            "injected temporary create failure",
            Some("payload.artifact"),
        ));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|error| {
            artifact_link_failure(
                "artifact_link_temp_write_failed",
                error.to_string(),
                Some("payload.artifact"),
            )
        })?;
    let write_result = if fault == ArtifactLinkFault::TempWrite {
        Err(std::io::Error::other("injected temporary write failure"))
    } else {
        file.write_all(&bytes)
    }
    .and_then(|_| {
        if fault == ArtifactLinkFault::TempFlush {
            Err(std::io::Error::other("injected temporary flush failure"))
        } else {
            file.flush()
        }
    })
    .and_then(|_| {
        if fault == ArtifactLinkFault::TempFsync {
            Err(std::io::Error::other("injected temporary fsync failure"))
        } else {
            file.sync_all()
        }
    });
    if let Err(error) = write_result {
        let _ = fs::remove_file(&temp);
        return Err(artifact_link_failure(
            "artifact_link_temp_write_failed",
            error.to_string(),
            Some("payload.artifact"),
        ));
    }
    drop(file);
    let temp_bytes = fs::read(&temp).map_err(|error| {
        let _ = fs::remove_file(&temp);
        artifact_link_failure(
            "artifact_link_temp_write_failed",
            error.to_string(),
            Some("payload.artifact"),
        )
    })?;
    if temp_bytes != bytes {
        let _ = fs::remove_file(&temp);
        return Err(artifact_link_failure(
            "artifact_link_temp_write_failed",
            "temporary sidecar bytes do not match",
            Some("payload.artifact"),
        ));
    }
    let snapshot_changed = fault == ArtifactLinkFault::SnapshotBeforePublish
        || fs::read(&artifact_path).ok().as_deref() != Some(artifact_bytes_before.as_slice())
        || fs::read(&ledger_path).ok().as_deref() != Some(ledger_bytes_before.as_slice());
    if snapshot_changed {
        let _ = fs::remove_file(&temp);
        return Err(artifact_link_failure(
            "artifact_link_snapshot_changed",
            "artifact or ledger changed before publication",
            Some("payload"),
        ));
    }
    let recheck_connection = Connection::open_with_flags(&ledger_path, flags).map_err(|_| {
        let _ = fs::remove_file(&temp);
        artifact_link_failure(
            "artifact_link_ledger_invalid",
            "ledger cannot be reopened for snapshot validation",
            Some("payload"),
        )
    })?;
    let (rechecked_rows, _) = validate_exact_read_only_ledger(&recheck_connection, &request.run_id)
        .map_err(|message| {
            let _ = fs::remove_file(&temp);
            artifact_link_failure("artifact_link_ledger_invalid", message, Some("payload"))
        })?;
    drop(recheck_connection);
    if fs::symlink_metadata(&target).is_ok() {
        let _ = fs::remove_file(&temp);
        return Err(artifact_link_failure(
            "artifact_link_target_exists",
            "artifact sidecar was created concurrently",
            Some("payload.artifact.path"),
        ));
    }
    if rechecked_rows != ledger_rows_before
        || ["-journal", "-wal", "-shm"].iter().any(|suffix| {
            fs::symlink_metadata(ledger_path.with_file_name(format!("ledger.sqlite{suffix}")))
                .is_ok()
        })
    {
        let _ = fs::remove_file(&temp);
        return Err(artifact_link_failure(
            "artifact_link_snapshot_changed",
            "validated state changed before publication",
            Some("payload"),
        ));
    }
    if fault == ArtifactLinkFault::Publish {
        let _ = fs::remove_file(&temp);
        return Err(artifact_link_failure(
            "artifact_link_publish_failed",
            "injected atomic publication failure",
            Some("payload.artifact.path"),
        ));
    }
    if let Err(error) = fs::hard_link(&temp, &target) {
        let _ = fs::remove_file(&temp);
        let code = if error.kind() == std::io::ErrorKind::AlreadyExists {
            "artifact_link_target_exists"
        } else {
            "artifact_link_publish_failed"
        };
        return Err(artifact_link_failure(
            code,
            error.to_string(),
            Some("payload.artifact.path"),
        ));
    }
    if fault == ArtifactLinkFault::TempCleanup || fs::remove_file(&temp).is_err() {
        return Err(ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_temp_cleanup_warning",
            message: "temporary file cleanup failed after publication".to_owned(),
            path: Some("payload.artifact.path"),
            mutation_performed: true,
        });
    }
    let directory_sync = if fault == ArtifactLinkFault::DirectoryFsync {
        Err(std::io::Error::other("injected directory fsync failure"))
    } else {
        File::open(parent).and_then(|directory| directory.sync_all())
    };
    match directory_sync {
        Ok(()) => {}
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::Unsupported | std::io::ErrorKind::InvalidInput
            ) => {}
        Err(error) => {
            return Err(ArtifactLinkFailure {
                status: KernelResponseStatus::Error,
                code: "artifact_link_durability_uncertain",
                message: error.to_string(),
                path: Some("payload.artifact.path"),
                mutation_performed: true,
            })
        }
    }
    if fault == ArtifactLinkFault::FinalRead {
        return Err(ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_postcondition_failed",
            message: "injected final read failure".to_owned(),
            path: Some("payload.artifact.path"),
            mutation_performed: true,
        });
    }
    let final_meta = fs::symlink_metadata(&target).map_err(|error| ArtifactLinkFailure {
        status: KernelResponseStatus::Error,
        code: "artifact_link_postcondition_failed",
        message: error.to_string(),
        path: Some("payload.artifact.path"),
        mutation_performed: true,
    })?;
    if !final_meta.is_file() || final_meta.file_type().is_symlink() {
        return Err(ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_postcondition_failed",
            message: "published sidecar is not a regular file".to_owned(),
            path: Some("payload.artifact.path"),
            mutation_performed: true,
        });
    }
    let observed = fs::read(&target).map_err(|error| ArtifactLinkFailure {
        status: KernelResponseStatus::Error,
        code: "artifact_link_postcondition_failed",
        message: error.to_string(),
        path: Some("payload.artifact.path"),
        mutation_performed: true,
    })?;
    if observed != bytes {
        return Err(ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_postcondition_failed",
            message: "sidecar bytes do not match request".to_owned(),
            path: Some("payload.artifact.path"),
            mutation_performed: true,
        });
    }
    let post_snapshot_changed = fault == ArtifactLinkFault::SnapshotAfterPublish
        || fs::read(&artifact_path).ok().as_deref() != Some(artifact_bytes_before.as_slice())
        || fs::read(&ledger_path).ok().as_deref() != Some(ledger_bytes_before.as_slice());
    if post_snapshot_changed {
        return Err(ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_snapshot_changed",
            message: "artifact or ledger changed after publication".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    let post_connection =
        Connection::open_with_flags(&ledger_path, flags).map_err(|_| ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_postcondition_failed",
            message: "ledger cannot be reopened after publication".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        })?;
    let (post_rows, _) = validate_exact_read_only_ledger(&post_connection, &request.run_id)
        .map_err(|message| ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_postcondition_failed",
            message,
            path: Some("payload"),
            mutation_performed: true,
        })?;
    if post_rows != ledger_rows_before {
        return Err(ArtifactLinkFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_link_snapshot_changed",
            message: "ledger semantic snapshot changed after publication".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    Ok((
        map_value([
            ("artifact", linked),
            (
                "sidecar_path",
                Value::String(format!("{}.meta.json", request.artifact.path)),
            ),
            ("sidecar_content_hash", Value::String(sha256_hex(&bytes))),
            ("bytes_written", Value::Number((bytes.len() as u64).into())),
            ("created", Value::Bool(true)),
            ("linked_to_ledger", Value::Bool(true)),
            ("authority_granted", Value::Bool(false)),
        ]),
        Vec::new(),
    ))
}

#[derive(Debug)]
struct BundlePlan {
    request: PersistenceCommitBundlePayload,
    linked_refs: Vec<Value>,
    artifact_bytes: Vec<Vec<u8>>,
    sidecar_bytes: Vec<Vec<u8>>,
    artifact_paths: Vec<PathBuf>,
    sidecar_paths: Vec<PathBuf>,
    new_commit_hash: String,
    previous_tip_hash: String,
    commit_payload_json: String,
    fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum BundleSnapshotEntry {
    Directory,
    File(Vec<u8>),
    Symlink(PathBuf),
    Other,
}

type BundleSnapshot = BTreeMap<PathBuf, BundleSnapshotEntry>;

fn snapshot_bundle_tree(run_dir: &Path) -> Result<BundleSnapshot, PersistenceBundleFailure> {
    fn visit(
        base: &Path,
        directory: &Path,
        snapshot: &mut BundleSnapshot,
    ) -> Result<(), PersistenceBundleFailure> {
        let mut entries = fs::read_dir(directory)
            .map_err(|_| {
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree cannot be enumerated",
                    Some("payload.run_id"),
                )
            })?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| {
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree entry cannot be inspected",
                    Some("payload.run_id"),
                )
            })?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let path = entry.path();
            let relative = path
                .strip_prefix(base)
                .expect("snapshot path beneath run")
                .to_path_buf();
            let metadata = fs::symlink_metadata(&path).map_err(|_| {
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree metadata changed during snapshot",
                    Some("payload.run_id"),
                )
            })?;
            let value = if metadata.file_type().is_symlink() {
                BundleSnapshotEntry::Symlink(fs::read_link(&path).map_err(|_| {
                    persistence_bundle_failure(
                        "persistence_bundle_snapshot_changed",
                        "run tree symlink changed during snapshot",
                        Some("payload.run_id"),
                    )
                })?)
            } else if metadata.is_dir() {
                BundleSnapshotEntry::Directory
            } else if metadata.is_file() {
                BundleSnapshotEntry::File(fs::read(&path).map_err(|_| {
                    persistence_bundle_failure(
                        "persistence_bundle_snapshot_changed",
                        "run tree file changed during snapshot",
                        Some("payload.run_id"),
                    )
                })?)
            } else {
                BundleSnapshotEntry::Other
            };
            snapshot.insert(relative, value);
            if metadata.is_dir() && !metadata.file_type().is_symlink() {
                visit(base, &path, snapshot)?;
            }
        }
        Ok(())
    }

    let mut snapshot = BundleSnapshot::new();
    visit(run_dir, run_dir, &mut snapshot)?;
    Ok(snapshot)
}

fn bundle_snapshot_matches(run_dir: &Path, expected: &BundleSnapshot) -> bool {
    snapshot_bundle_tree(run_dir).is_ok_and(|observed| observed == *expected)
}

fn bundle_snapshot_has_temp(snapshot: &BundleSnapshot) -> bool {
    snapshot.keys().any(|path| {
        path.file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|name| {
                name.starts_with(".commit-bundle-intent-") || name.contains(".tmp-bundle-")
            })
    })
}

fn insert_bundle_snapshot_file(
    snapshot: &mut BundleSnapshot,
    run_dir: &Path,
    path: &Path,
    bytes: &[u8],
) {
    snapshot.insert(
        path.strip_prefix(run_dir)
            .expect("bundle output beneath run")
            .to_path_buf(),
        BundleSnapshotEntry::File(bytes.to_vec()),
    );
}

fn validate_commit_bundle_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    if payload.len() != 9
        || !payload.contains_key("run_id")
        || !payload.contains_key("expected_tip_hash")
        || !payload.contains_key("artifacts")
        || !payload.contains_key("action_type")
        || !payload.contains_key("commit_payload")
        || !payload.contains_key("candidate_id_optional")
        || !payload.contains_key("timestamp")
        || !payload.contains_key("overwrite_policy")
        || !payload.contains_key("recovery_policy")
    {
        return Err(
            "persistence.commit_bundle payload must contain exactly nine fields".to_owned(),
        );
    }
    let request =
        serde_json::from_value::<PersistenceCommitBundlePayload>(Value::Object(payload.clone()))
            .map_err(|error| error.to_string())?;
    if !is_safe_segment(&request.run_id)
        || !is_sha256_hex(&request.expected_tip_hash)
        || !is_valid_action_type(&request.action_type)
        || request.action_type == "InitRun"
        || request.overwrite_policy != "FailIfExists"
        || request.recovery_policy != "ResumeExact"
    {
        return Err("bundle identifiers, action, hash, or policy is invalid".to_owned());
    }
    if request.artifacts.is_empty() || request.artifacts.len() > 16 {
        return Err("bundle must contain one to sixteen artifacts".to_owned());
    }
    if request
        .candidate_id_optional
        .as_deref()
        .is_some_and(|value| !is_safe_segment(value))
    {
        return Err("candidate_id_optional must use safe segment syntax".to_owned());
    }
    if !validate_utc_timestamp(&request.timestamp) {
        return Err("timestamp must be a real ASCII UTC timestamp".to_owned());
    }
    let mut ids = HashSet::new();
    let mut paths = HashSet::new();
    let mut aggregate = 0usize;
    for item in &request.artifacts {
        if !is_safe_segment(&item.artifact_id)
            || item
                .filename_stem_optional
                .as_deref()
                .is_some_and(|value| !is_safe_segment(value))
        {
            return Err("bundle artifact identifiers must use safe segment syntax".to_owned());
        }
        let directory = artifact_directory(&item.artifact_type)
            .ok_or_else(|| "artifact_type is not supported".to_owned())?;
        if !ids.insert(item.artifact_id.clone()) {
            return Err("bundle artifact IDs must be unique".to_owned());
        }
        let stem = item
            .filename_stem_optional
            .as_deref()
            .unwrap_or(&item.artifact_id);
        let relative = format!("runs/{}/{directory}/{stem}.json", request.run_id);
        if !paths.insert(relative) {
            return Err("bundle artifact paths must be unique".to_owned());
        }
        if item.metadata.len() > 64
            || item.metadata.keys().any(|key| key.len() > 128)
            || item.metadata.keys().any(|key| {
                matches!(
                    key.as_str(),
                    "format" | "producer" | "is_verification_evidence"
                )
            })
            || scan_forbidden_authority_values(&Value::Object(item.metadata.clone()))
        {
            return Err(
                "bundle metadata exceeds bounds or contains forbidden authority".to_owned(),
            );
        }
        let metadata_json = canonical_json(&Value::Object(item.metadata.clone()))
            .map_err(|error| error.to_string())?;
        if metadata_json.len() > 64 * 1024 {
            return Err("bundle metadata exceeds 64 KiB serialized size".to_owned());
        }
        let artifact_json = canonical_json(&item.json_value).map_err(|error| error.to_string())?;
        let size = artifact_json.len() + 1;
        if size > 12 * 1024 * 1024 {
            return Err("serialized JSON payload exceeds 12 MiB".to_owned());
        }
        aggregate = aggregate
            .checked_add(size)
            .ok_or_else(|| "aggregate bundle size exceeds limit".to_owned())?;
    }
    let commit_payload_json = canonical_json(&Value::Object(request.commit_payload.clone()))
        .map_err(|error| error.to_string())?;
    if commit_payload_json.len() > 4 * 1024 * 1024 {
        return Err("serialized commit payload exceeds 4 MiB".to_owned());
    }
    if aggregate > 12 * 1024 * 1024 {
        return Err("aggregate artifact payload exceeds 12 MiB".to_owned());
    }
    Ok(())
}

fn build_bundle_plan(
    payload: &Map<String, Value>,
    root: &Path,
    old_rows: &[RawLedgerRow],
) -> Result<BundlePlan, PersistenceBundleFailure> {
    let request =
        serde_json::from_value::<PersistenceCommitBundlePayload>(Value::Object(payload.clone()))
            .map_err(|error| {
                persistence_bundle_failure(
                    "persistence_bundle_payload_invalid",
                    error.to_string(),
                    Some("payload"),
                )
            })?;
    validate_commit_bundle_payload_structure(payload).map_err(|message| {
        let code = if message.contains("MiB") || message.contains("KiB") {
            "persistence_bundle_size_exceeded"
        } else if message.contains("unique") {
            "persistence_bundle_duplicate"
        } else {
            "persistence_bundle_payload_invalid"
        };
        persistence_bundle_failure(code, message, Some("payload"))
    })?;
    let previous_tip_hash = request.expected_tip_hash.clone();
    if old_rows.is_empty() {
        return Err(persistence_bundle_failure(
            "persistence_bundle_ledger_invalid",
            "ledger is empty",
            Some("payload"),
        ));
    }
    let runs_root = root.join("runs");
    let run_dir = runs_root.join(&request.run_id);
    let mut artifact_refs = Vec::with_capacity(request.artifacts.len());
    let mut artifact_bytes = Vec::with_capacity(request.artifacts.len());
    let mut artifact_paths = Vec::with_capacity(request.artifacts.len());
    let mut sidecar_paths = Vec::with_capacity(request.artifacts.len());
    for item in &request.artifacts {
        let directory = artifact_directory(&item.artifact_type).expect("validated type");
        let stem = item
            .filename_stem_optional
            .as_deref()
            .unwrap_or(&item.artifact_id);
        let path = run_dir.join(directory).join(format!("{stem}.json"));
        let sidecar = PathBuf::from(format!("{}.meta.json", path.to_string_lossy()));
        let canonical = canonical_json(&item.json_value).map_err(|error| {
            persistence_bundle_failure(
                "persistence_bundle_payload_invalid",
                error.to_string(),
                Some("payload.artifacts.json_value"),
            )
        })?;
        let bytes = format!("{canonical}\n").into_bytes();
        let mut metadata = item.metadata.clone();
        metadata.insert("format".to_owned(), Value::String("json".to_owned()));
        metadata.insert("is_verification_evidence".to_owned(), Value::Bool(false));
        artifact_refs.push(Value::Object(map_value([
            ("id", Value::String(item.artifact_id.clone())),
            ("type", Value::String(item.artifact_type.clone())),
            (
                "path",
                Value::String(format!("runs/{}/{directory}/{stem}.json", request.run_id)),
            ),
            ("content_hash", Value::String(sha256_hex(&bytes))),
            ("producing_commit_hash", Value::Null),
            ("metadata", Value::Object(metadata)),
        ])));
        artifact_bytes.push(bytes);
        artifact_paths.push(path);
        sidecar_paths.push(sidecar);
    }
    let hash_refs: Vec<Value> = artifact_refs
        .iter()
        .map(|value| {
            let mut object = value.as_object().expect("artifact object").clone();
            object.insert(
                "producing_commit_hash".to_owned(),
                Value::String("<self>".to_owned()),
            );
            Value::Object(object)
        })
        .collect();
    let hash_value = map_value([
        ("parent_hash", Value::String(previous_tip_hash.clone())),
        ("run_id", Value::String(request.run_id.clone())),
        (
            "candidate_id",
            request
                .candidate_id_optional
                .clone()
                .map_or(Value::Null, Value::String),
        ),
        ("action_type", Value::String(request.action_type.clone())),
        ("payload", Value::Object(request.commit_payload.clone())),
        ("artifact_refs", Value::Array(hash_refs)),
        ("timestamp", Value::String(request.timestamp.clone())),
    ]);
    let new_commit_hash = sha256_hex(
        canonical_json(&Value::Object(hash_value))
            .map_err(|error| {
                persistence_bundle_failure(
                    "persistence_bundle_payload_invalid",
                    error.to_string(),
                    Some("payload"),
                )
            })?
            .as_bytes(),
    );
    let linked_refs: Vec<Value> = artifact_refs
        .iter()
        .map(|value| {
            let mut object = value.as_object().expect("artifact object").clone();
            object.insert(
                "producing_commit_hash".to_owned(),
                Value::String(new_commit_hash.clone()),
            );
            Value::Object(object)
        })
        .collect();
    let sidecar_bytes = linked_refs
        .iter()
        .map(|value| {
            canonical_json(value)
                .map(|canonical| format!("{canonical}\n").into_bytes())
                .map_err(|error| {
                    persistence_bundle_failure(
                        "persistence_bundle_payload_invalid",
                        error.to_string(),
                        Some("payload"),
                    )
                })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let fingerprint_value = Value::Object(map_value([
        (
            "protocol_version",
            Value::String(PROTOCOL_VERSION.to_owned()),
        ),
        (
            "operation",
            Value::String("persistence.commit_bundle".to_owned()),
        ),
        ("payload", Value::Object(payload.clone())),
    ]));
    let fingerprint = sha256_hex(
        canonical_json(&fingerprint_value)
            .map_err(|error| {
                persistence_bundle_failure(
                    "persistence_bundle_payload_invalid",
                    error.to_string(),
                    Some("payload"),
                )
            })?
            .as_bytes(),
    );
    let combined_output_bytes = artifact_bytes
        .iter()
        .chain(&sidecar_bytes)
        .try_fold(0usize, |total, bytes| total.checked_add(bytes.len()))
        .ok_or_else(|| {
            persistence_bundle_failure(
                "persistence_bundle_size_exceeded",
                "aggregate artifact and sidecar payload exceeds 12 MiB",
                Some("payload.artifacts"),
            )
        })?;
    if combined_output_bytes > 12 * 1024 * 1024 {
        return Err(persistence_bundle_failure(
            "persistence_bundle_size_exceeded",
            "aggregate artifact and sidecar payload exceeds 12 MiB",
            Some("payload.artifacts"),
        ));
    }
    let commit_payload_json = canonical_json(&Value::Object(request.commit_payload.clone()))
        .expect("validated commit payload");
    Ok(BundlePlan {
        request,
        linked_refs,
        artifact_bytes,
        sidecar_bytes,
        artifact_paths,
        sidecar_paths,
        new_commit_hash,
        previous_tip_hash,
        commit_payload_json,
        fingerprint,
    })
}

fn bundle_intent_value(plan: &BundlePlan, root: &Path) -> Value {
    let outputs = plan
        .artifact_paths
        .iter()
        .zip(&plan.artifact_bytes)
        .zip(&plan.sidecar_paths)
        .zip(&plan.sidecar_bytes)
        .map(
            |(((artifact_path, artifact_bytes), sidecar_path), sidecar_bytes)| {
                Value::Object(map_value([
                    (
                        "artifact_path",
                        Value::String(
                            artifact_path
                                .strip_prefix(root)
                                .expect("bundle path beneath root")
                                .to_string_lossy()
                                .replace('\\', "/"),
                        ),
                    ),
                    (
                        "artifact_length",
                        Value::Number((artifact_bytes.len() as u64).into()),
                    ),
                    ("artifact_hash", Value::String(sha256_hex(artifact_bytes))),
                    (
                        "sidecar_path",
                        Value::String(
                            sidecar_path
                                .strip_prefix(root)
                                .expect("bundle path beneath root")
                                .to_string_lossy()
                                .replace('\\', "/"),
                        ),
                    ),
                    (
                        "sidecar_length",
                        Value::Number((sidecar_bytes.len() as u64).into()),
                    ),
                    ("sidecar_hash", Value::String(sha256_hex(sidecar_bytes))),
                ]))
            },
        )
        .collect::<Vec<_>>();
    Value::Object(map_value([
        (
            "protocol_version",
            Value::String(PROTOCOL_VERSION.to_owned()),
        ),
        (
            "operation",
            Value::String("persistence.commit_bundle".to_owned()),
        ),
        ("fingerprint", Value::String(plan.fingerprint.clone())),
        (
            "expected_tip_hash",
            Value::String(plan.previous_tip_hash.clone()),
        ),
        (
            "new_commit_hash",
            Value::String(plan.new_commit_hash.clone()),
        ),
        ("outputs", Value::Array(outputs)),
    ]))
}

fn bundle_intent_bytes(plan: &BundlePlan, root: &Path) -> Vec<u8> {
    format!(
        "{}\n",
        canonical_json(&bundle_intent_value(plan, root)).expect("validated bundle intent")
    )
    .into_bytes()
}

fn bundle_temp_nonce(path: &Path, counter: u64) -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    let seed = format!("{}:{counter}:{now}:{file_name}", std::process::id());
    sha256_hex(seed.as_bytes())[..24].to_owned()
}

fn write_bundle_intent(
    path: &Path,
    value: &Value,
    fault: PersistenceBundleFault,
) -> Result<(), PersistenceBundleFailure> {
    let parent = path.parent().ok_or_else(|| {
        persistence_bundle_failure(
            "persistence_bundle_directory_invalid",
            "bundle intent parent is missing",
            Some("payload.run_id"),
        )
    })?;
    let canonical = canonical_json(value).map_err(|error| {
        persistence_bundle_failure(
            "persistence_bundle_intent_write_failed",
            error.to_string(),
            Some("payload.run_id"),
        )
    })?;
    static INTENT_COUNTER: AtomicU64 = AtomicU64::new(0);
    let counter = INTENT_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nonce = bundle_temp_nonce(path, counter);
    let temp = parent.join(format!(".commit-bundle-intent-{nonce}"));
    let bytes = format!("{canonical}\n").into_bytes();
    let mut published = false;
    let result = (|| {
        if fault == PersistenceBundleFault::IntentTempCreate {
            return Err("injected intent temporary-create fault".to_owned());
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|error| error.to_string())?;
        if fault == PersistenceBundleFault::IntentTempWrite {
            return Err("injected intent temporary-write fault".to_owned());
        }
        file.write_all(&bytes).map_err(|error| error.to_string())?;
        if fault == PersistenceBundleFault::IntentTempFlush {
            return Err("injected intent temporary-flush fault".to_owned());
        }
        file.flush().map_err(|error| error.to_string())?;
        if fault == PersistenceBundleFault::IntentTempFsync {
            return Err("injected intent temporary-fsync fault".to_owned());
        }
        file.sync_all().map_err(|error| error.to_string())?;
        drop(file);
        if fault == PersistenceBundleFault::IntentPublish {
            return Err("injected intent publish fault".to_owned());
        }
        fs::hard_link(&temp, path).map_err(|error| error.to_string())?;
        published = true;
        fs::remove_file(&temp).map_err(|error| error.to_string())?;
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        if fault == PersistenceBundleFault::IntentReadback {
            return Err("injected intent readback fault".to_owned());
        }
        if fs::read(path).ok().as_deref() != Some(bytes.as_slice()) {
            return Err("intent bytes do not match".to_owned());
        }
        Ok::<(), String>(())
    })();
    if let Err(message) = result {
        let mut cleanup_ok = true;
        if fs::symlink_metadata(&temp).is_ok() && fs::remove_file(&temp).is_err() {
            cleanup_ok = false;
        }
        if published && fs::remove_file(path).is_err() {
            cleanup_ok = false;
        }
        if File::open(parent)
            .and_then(|directory| directory.sync_all())
            .is_err()
        {
            cleanup_ok = false;
        }
        if !cleanup_ok {
            return Err(PersistenceBundleFailure {
                status: KernelResponseStatus::Error,
                code: "persistence_bundle_rollback_uncertain",
                message: "bundle intent publication could not be rolled back exactly".to_owned(),
                path: Some("payload.run_id"),
                mutation_performed: true,
            });
        }
        return Err(persistence_bundle_failure(
            "persistence_bundle_intent_write_failed",
            message,
            Some("payload.run_id"),
        ));
    }
    Ok(())
}

fn publish_bundle_file(
    path: &Path,
    bytes: &[u8],
    kind: &'static str,
    allow_existing_exact: bool,
    fault: PersistenceBundleFault,
) -> Result<bool, PersistenceBundleFailure> {
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(persistence_bundle_failure(
                "persistence_bundle_target_exists",
                "bundle destination is already occupied",
                Some("payload.artifacts"),
            ));
        }
        if !allow_existing_exact {
            return Err(persistence_bundle_failure(
                "persistence_bundle_target_exists",
                "fresh bundle destination already exists",
                Some("payload.artifacts"),
            ));
        }
        let observed = fs::read(path).map_err(|error| {
            persistence_bundle_failure(
                "persistence_bundle_target_exists",
                error.to_string(),
                Some("payload.artifacts"),
            )
        })?;
        if observed != bytes {
            return Err(persistence_bundle_failure(
                "persistence_bundle_target_exists",
                "bundle destination bytes differ",
                Some("payload.artifacts"),
            ));
        }
        return Ok(false);
    }
    let parent = path.parent().ok_or_else(|| {
        persistence_bundle_failure(
            "persistence_bundle_directory_invalid",
            "bundle destination parent is missing",
            Some("payload.artifacts"),
        )
    })?;
    static BUNDLE_TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);
    let counter = BUNDLE_TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nonce = bundle_temp_nonce(path, counter);
    let temp = parent.join(format!(
        ".{}.tmp-bundle-{}",
        path.file_name()
            .and_then(|v| v.to_str())
            .unwrap_or("artifact"),
        nonce
    ));
    let is_artifact = kind == "artifact";
    let selected = |artifact_fault, sidecar_fault| {
        fault
            == if is_artifact {
                artifact_fault
            } else {
                sidecar_fault
            }
    };
    let mut published = false;
    let result = (|| {
        if selected(
            PersistenceBundleFault::ArtifactTempCreate,
            PersistenceBundleFault::SidecarTempCreate,
        ) {
            return Err("injected bundle temporary-create fault".to_owned());
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|error| error.to_string())?;
        if selected(
            PersistenceBundleFault::ArtifactTempWrite,
            PersistenceBundleFault::SidecarTempWrite,
        ) {
            return Err("injected bundle temporary-write fault".to_owned());
        }
        file.write_all(bytes).map_err(|error| error.to_string())?;
        if selected(
            PersistenceBundleFault::ArtifactTempFlush,
            PersistenceBundleFault::SidecarTempFlush,
        ) {
            return Err("injected bundle temporary-flush fault".to_owned());
        }
        file.flush().map_err(|error| error.to_string())?;
        if selected(
            PersistenceBundleFault::ArtifactTempFsync,
            PersistenceBundleFault::SidecarTempFsync,
        ) {
            return Err("injected bundle temporary-fsync fault".to_owned());
        }
        file.sync_all().map_err(|error| error.to_string())?;
        drop(file);
        if fs::read(&temp).ok().as_deref() != Some(bytes) {
            return Err("temporary bytes do not match".to_owned());
        }
        if selected(
            PersistenceBundleFault::ArtifactPublish,
            PersistenceBundleFault::SidecarPublish,
        ) {
            return Err("injected bundle publish fault".to_owned());
        }
        fs::hard_link(&temp, path).map_err(|error| error.to_string())?;
        published = true;
        fs::remove_file(&temp).map_err(|error| error.to_string())?;
        if selected(
            PersistenceBundleFault::ArtifactDirectoryFsync,
            PersistenceBundleFault::SidecarDirectoryFsync,
        ) {
            return Err("injected bundle directory-fsync fault".to_owned());
        }
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        if selected(
            PersistenceBundleFault::ArtifactReadback,
            PersistenceBundleFault::SidecarReadback,
        ) {
            return Err("injected bundle final-readback fault".to_owned());
        }
        if !bundle_file_matches(path, bytes) {
            return Err("published bundle bytes do not match".to_owned());
        }
        Ok::<(), String>(())
    })();
    if let Err(message) = result {
        let mut cleanup_ok = true;
        if fs::symlink_metadata(&temp).is_ok() && fs::remove_file(&temp).is_err() {
            cleanup_ok = false;
        }
        if published && fs::remove_file(path).is_err() {
            cleanup_ok = false;
        }
        if File::open(parent)
            .and_then(|directory| directory.sync_all())
            .is_err()
        {
            cleanup_ok = false;
        }
        if !cleanup_ok {
            return Err(PersistenceBundleFailure {
                status: KernelResponseStatus::Error,
                code: "persistence_bundle_rollback_uncertain",
                message: format!("{kind} publication cleanup could not be proven"),
                path: Some("payload.artifacts"),
                mutation_performed: true,
            });
        }
        let mut error = persistence_bundle_failure(
            "persistence_bundle_publish_failed",
            format!("{kind}: {message}"),
            Some("payload.artifacts"),
        );
        if allow_existing_exact && published {
            error.status = KernelResponseStatus::Error;
            error.code = "persistence_bundle_recovery_conflict";
            error.mutation_performed = true;
        }
        return Err(error);
    }
    Ok(true)
}

fn bundle_file_matches(path: &Path, expected: &[u8]) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| {
        metadata.is_file()
            && !metadata.file_type().is_symlink()
            && metadata.len() == expected.len() as u64
            && fs::read(path).is_ok_and(|observed| {
                observed == expected && sha256_hex(&observed) == sha256_hex(expected)
            })
    })
}

#[derive(Clone, Copy)]
enum BundleRollbackIntentState {
    Absent,
    Fresh,
    Recovery,
}

fn rollback_bundle_failure(
    connection: &Connection,
    run_dir: &Path,
    intent_path: &Path,
    initial_snapshot: &BundleSnapshot,
    created_paths: &[PathBuf],
    intent_state: BundleRollbackIntentState,
    mut original: PersistenceBundleFailure,
) -> PersistenceBundleFailure {
    let mut cleanup_ok = connection.execute_batch("ROLLBACK").is_ok();
    for path in created_paths.iter().rev() {
        if fs::remove_file(path).is_err() {
            cleanup_ok = false;
        }
    }
    let mut directories = BTreeSet::new();
    directories.insert(run_dir.to_path_buf());
    for path in created_paths {
        if let Some(parent) = path.parent() {
            directories.insert(parent.to_path_buf());
        }
    }
    for directory in directories {
        if File::open(directory)
            .and_then(|handle| handle.sync_all())
            .is_err()
        {
            cleanup_ok = false;
        }
    }
    if matches!(intent_state, BundleRollbackIntentState::Fresh) && cleanup_ok {
        let mut expected_with_intent = initial_snapshot.clone();
        let intent_relative = intent_path
            .strip_prefix(run_dir)
            .expect("intent path beneath run")
            .to_path_buf();
        match fs::symlink_metadata(intent_path) {
            Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => {
                match fs::read(intent_path) {
                    Ok(bytes) => {
                        expected_with_intent
                            .insert(intent_relative, BundleSnapshotEntry::File(bytes));
                    }
                    Err(_) => cleanup_ok = false,
                }
            }
            Ok(_) => cleanup_ok = false,
            Err(_) => {}
        }
        cleanup_ok &= bundle_snapshot_matches(run_dir, &expected_with_intent);
        if cleanup_ok
            && fs::symlink_metadata(intent_path).is_ok()
            && fs::remove_file(intent_path).is_err()
        {
            cleanup_ok = false;
        }
        if cleanup_ok
            && File::open(run_dir)
                .and_then(|handle| handle.sync_all())
                .is_err()
        {
            cleanup_ok = false;
        }
    }
    cleanup_ok &= bundle_snapshot_matches(run_dir, initial_snapshot);
    if !cleanup_ok {
        return PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_rollback_uncertain",
            message: "bundle rollback could not be proven exact".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        };
    }
    if matches!(intent_state, BundleRollbackIntentState::Recovery) && !created_paths.is_empty() {
        original.status = KernelResponseStatus::Error;
        original.code = "persistence_bundle_recovery_conflict";
        original.mutation_performed = true;
    }
    original
}

fn commit_bundle_result(
    plan: &BundlePlan,
    count_before: usize,
    recovered: bool,
) -> Map<String, Value> {
    let commit = map_value([
        ("commit_hash", Value::String(plan.new_commit_hash.clone())),
        ("parent_hash", Value::String(plan.previous_tip_hash.clone())),
        ("run_id", Value::String(plan.request.run_id.clone())),
        (
            "candidate_id",
            plan.request
                .candidate_id_optional
                .clone()
                .map_or(Value::Null, Value::String),
        ),
        (
            "action_type",
            Value::String(plan.request.action_type.clone()),
        ),
        (
            "payload",
            Value::Object(plan.request.commit_payload.clone()),
        ),
        ("artifact_refs", Value::Array(plan.linked_refs.clone())),
        ("timestamp", Value::String(plan.request.timestamp.clone())),
    ]);
    map_value([
        ("artifacts", Value::Array(plan.linked_refs.clone())),
        ("commit", Value::Object(commit)),
        (
            "previous_tip_hash",
            Value::String(plan.previous_tip_hash.clone()),
        ),
        ("new_tip_hash", Value::String(plan.new_commit_hash.clone())),
        (
            "commit_count_before",
            Value::Number((count_before as u64).into()),
        ),
        (
            "commit_count_after",
            Value::Number(((count_before + 1) as u64).into()),
        ),
        (
            "artifact_count",
            Value::Number((plan.linked_refs.len() as u64).into()),
        ),
        (
            "sidecar_count",
            Value::Number((plan.linked_refs.len() as u64).into()),
        ),
        ("bundle_committed", Value::Bool(true)),
        ("recovered_from_intent", Value::Bool(recovered)),
        ("authority_granted", Value::Bool(false)),
    ])
}

fn bundle_expected_row(plan: &BundlePlan) -> RawLedgerRow {
    RawLedgerRow {
        commit_hash: plan.new_commit_hash.clone(),
        parent_hash: Some(plan.previous_tip_hash.clone()),
        run_id: plan.request.run_id.clone(),
        candidate_id: plan.request.candidate_id_optional.clone(),
        action_type: plan.request.action_type.clone(),
        payload_json: plan.commit_payload_json.clone(),
        artifact_refs_json: canonical_json(&Value::Array(plan.linked_refs.clone()))
            .expect("validated bundle references"),
        timestamp: plan.request.timestamp.clone(),
    }
}

fn commit_bundle_payload(
    payload: &Map<String, Value>,
    project_root: &Path,
) -> Result<Map<String, Value>, PersistenceBundleFailure> {
    commit_bundle_payload_with_fault(payload, project_root, PersistenceBundleFault::None)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
enum PersistenceBundleFault {
    None,
    IntentTempCreate,
    IntentTempWrite,
    IntentTempFlush,
    IntentTempFsync,
    IntentPublish,
    IntentReadback,
    SnapshotBeforeArtifact,
    ArtifactTempCreate,
    ArtifactTempWrite,
    ArtifactTempFlush,
    ArtifactTempFsync,
    ArtifactPublish,
    ArtifactDirectoryFsync,
    ArtifactReadback,
    ArtifactAfterPublish,
    SnapshotBeforeSidecar,
    SidecarTempCreate,
    SidecarTempWrite,
    SidecarTempFlush,
    SidecarTempFsync,
    SidecarPublish,
    SidecarDirectoryFsync,
    SidecarReadback,
    SidecarAfterPublish,
    Insert,
    Readback,
    Rollback,
    Commit,
    Reopen,
    IntentCleanup,
    FinalPostcondition,
}

fn commit_bundle_payload_with_fault(
    payload: &Map<String, Value>,
    project_root: &Path,
    fault: PersistenceBundleFault,
) -> Result<Map<String, Value>, PersistenceBundleFailure> {
    let request =
        serde_json::from_value::<PersistenceCommitBundlePayload>(Value::Object(payload.clone()))
            .map_err(|error| {
                persistence_bundle_failure(
                    "persistence_bundle_payload_invalid",
                    error.to_string(),
                    Some("payload"),
                )
            })?;
    validate_commit_bundle_payload_structure(payload).map_err(|message| {
        let code = if message.contains("MiB") || message.contains("KiB") {
            "persistence_bundle_size_exceeded"
        } else if message.contains("unique") {
            "persistence_bundle_duplicate"
        } else {
            "persistence_bundle_payload_invalid"
        };
        persistence_bundle_failure(code, message, Some("payload"))
    })?;
    let root_meta = fs::symlink_metadata(project_root).map_err(|_| {
        persistence_bundle_failure(
            "persistence_bundle_run_missing",
            "project root is unavailable",
            Some("payload.run_id"),
        )
    })?;
    if !root_meta.is_dir() || root_meta.file_type().is_symlink() {
        return Err(persistence_bundle_failure(
            "persistence_bundle_directory_invalid",
            "project root is not a real directory",
            Some("payload.run_id"),
        ));
    }
    let root = fs::canonicalize(project_root).map_err(|_| {
        persistence_bundle_failure(
            "persistence_bundle_run_missing",
            "project root is unavailable",
            Some("payload.run_id"),
        )
    })?;
    let runs_root = root.join("runs");
    let run_dir = runs_root.join(&request.run_id);
    for directory in [&runs_root, &run_dir] {
        let metadata = fs::symlink_metadata(directory).map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_run_missing",
                "required run directory is missing",
                Some("payload.run_id"),
            )
        })?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err(persistence_bundle_failure(
                "persistence_bundle_directory_invalid",
                "required run directory is not a real directory",
                Some("payload.run_id"),
            ));
        }
    }
    for item in &request.artifacts {
        let directory = run_dir.join(artifact_directory(&item.artifact_type).expect("validated"));
        let metadata = fs::symlink_metadata(&directory).map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_directory_invalid",
                "required artifact directory is missing",
                Some("payload.artifacts"),
            )
        })?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err(persistence_bundle_failure(
                "persistence_bundle_directory_invalid",
                "required artifact directory is not a real directory",
                Some("payload.artifacts"),
            ));
        }
    }
    let ledger_path = run_dir.join("ledger.sqlite");
    let ledger_meta = fs::symlink_metadata(&ledger_path).map_err(|_| {
        persistence_bundle_failure(
            "persistence_bundle_ledger_invalid",
            "ledger is missing",
            Some("payload.run_id"),
        )
    })?;
    if !ledger_meta.is_file() || ledger_meta.file_type().is_symlink() {
        return Err(persistence_bundle_failure(
            "persistence_bundle_ledger_invalid",
            "ledger is not a regular file",
            Some("payload.run_id"),
        ));
    }
    for suffix in ["-journal", "-wal", "-shm"] {
        if fs::symlink_metadata(ledger_path.with_file_name(format!("ledger.sqlite{suffix}")))
            .is_ok()
        {
            return Err(persistence_bundle_failure(
                "persistence_bundle_ledger_invalid",
                "ledger auxiliary file exists",
                Some("payload.run_id"),
            ));
        }
    }
    let flags = OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let connection = Connection::open_with_flags(&ledger_path, flags).map_err(|error| {
        let code = if matches!(
            error,
            rusqlite::Error::SqliteFailure(ref failure, _)
                if matches!(
                    failure.code,
                    rusqlite::ffi::ErrorCode::DatabaseBusy
                        | rusqlite::ffi::ErrorCode::DatabaseLocked
                )
        ) {
            "persistence_bundle_busy"
        } else {
            "persistence_bundle_ledger_invalid"
        };
        persistence_bundle_failure(code, "ledger cannot be opened", Some("payload.run_id"))
    })?;
    let journal_mode_before: String = connection
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_ledger_invalid",
                "ledger journal mode cannot be read",
                Some("payload.run_id"),
            )
        })?;
    connection
        .execute_batch("PRAGMA foreign_keys=ON; PRAGMA busy_timeout=0; PRAGMA synchronous=FULL;")
        .map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_ledger_invalid",
                "ledger safety settings failed",
                Some("payload.run_id"),
            )
        })?;
    let connection_settings = (
        connection.query_row("PRAGMA foreign_keys", [], |row| row.get::<_, i64>(0)),
        connection.query_row("PRAGMA busy_timeout", [], |row| row.get::<_, i64>(0)),
        connection.query_row("PRAGMA synchronous", [], |row| row.get::<_, i64>(0)),
    );
    if !matches!(connection_settings, (Ok(1), Ok(0), Ok(2 | 3))) {
        return Err(persistence_bundle_failure(
            "persistence_bundle_ledger_invalid",
            "ledger connection safety settings are not active",
            Some("payload.run_id"),
        ));
    }
    match connection.execute_batch("BEGIN IMMEDIATE") {
        Ok(()) => {}
        Err(rusqlite::Error::SqliteFailure(ref failure, _))
            if matches!(
                failure.code,
                rusqlite::ffi::ErrorCode::DatabaseBusy | rusqlite::ffi::ErrorCode::DatabaseLocked
            ) =>
        {
            return Err(persistence_bundle_failure(
                "persistence_bundle_busy",
                "ledger is busy",
                Some("payload.expected_tip_hash"),
            ));
        }
        Err(_) => {
            return Err(persistence_bundle_failure(
                "persistence_bundle_ledger_invalid",
                "ledger transaction could not begin",
                Some("payload.run_id"),
            ));
        }
    }
    let (old_rows, old_ledger) = validate_exact_read_only_ledger(&connection, &request.run_id)
        .map_err(|message| {
            let _ = connection.execute_batch("ROLLBACK");
            persistence_bundle_failure(
                "persistence_bundle_ledger_invalid",
                message,
                Some("payload.run_id"),
            )
        })?;
    let plan = match build_bundle_plan(payload, &root, &old_rows) {
        Ok(plan) => plan,
        Err(error) => {
            let _ = connection.execute_batch("ROLLBACK");
            return Err(error);
        }
    };
    let initial_snapshot = match snapshot_bundle_tree(&run_dir) {
        Ok(snapshot) => snapshot,
        Err(error) => {
            let _ = connection.execute_batch("ROLLBACK");
            return Err(error);
        }
    };
    if bundle_snapshot_has_temp(&initial_snapshot) {
        let _ = connection.execute_batch("ROLLBACK");
        return Err(persistence_bundle_failure(
            "persistence_bundle_recovery_required",
            "bundle temporary object requires inspection",
            Some("payload.run_id"),
        ));
    }
    let mut expected_snapshot = initial_snapshot.clone();
    let intent_path = run_dir.join(".factori-commit-bundle.intent.json");
    let expected_intent_bytes = bundle_intent_bytes(&plan, &root);
    let intent_exists = fs::symlink_metadata(&intent_path).is_ok();
    let mut recovered = false;
    if intent_exists {
        let intent_metadata = fs::symlink_metadata(&intent_path).map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_recovery_invalid",
                "bundle intent cannot be inspected",
                Some("payload.run_id"),
            )
        })?;
        if !intent_metadata.is_file() || intent_metadata.file_type().is_symlink() {
            let _ = connection.execute_batch("ROLLBACK");
            return Err(persistence_bundle_failure(
                "persistence_bundle_recovery_invalid",
                "bundle intent must be a regular non-symlink file",
                Some("payload.run_id"),
            ));
        }
        let intent_bytes = fs::read(&intent_path).map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_recovery_invalid",
                "bundle intent cannot be read",
                Some("payload.run_id"),
            )
        })?;
        let intent_text = std::str::from_utf8(&intent_bytes).map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_recovery_invalid",
                "bundle intent is not valid UTF-8",
                Some("payload.run_id"),
            )
        })?;
        parse_json_without_duplicate_keys(intent_text).map_err(|_| {
            persistence_bundle_failure(
                "persistence_bundle_recovery_invalid",
                "bundle intent is not valid duplicate-free JSON",
                Some("payload.run_id"),
            )
        })?;
        if intent_bytes != expected_intent_bytes {
            let _ = connection.execute_batch("ROLLBACK");
            return Err(persistence_bundle_failure(
                "persistence_bundle_recovery_conflict",
                "existing bundle intent does not match request",
                Some("payload.run_id"),
            ));
        }
        recovered = true;
        for ((path, bytes), (sidecar, sidecar_bytes)) in plan
            .artifact_paths
            .iter()
            .zip(&plan.artifact_bytes)
            .zip(plan.sidecar_paths.iter().zip(&plan.sidecar_bytes))
        {
            for (output_path, expected_bytes) in [
                (path.as_path(), bytes.as_slice()),
                (sidecar.as_path(), sidecar_bytes.as_slice()),
            ] {
                if fs::symlink_metadata(output_path).is_ok()
                    && !bundle_file_matches(output_path, expected_bytes)
                {
                    let _ = connection.execute_batch("ROLLBACK");
                    return Err(persistence_bundle_failure(
                        "persistence_bundle_recovery_conflict",
                        "existing recovery output does not match the exact plan",
                        Some("payload.artifacts"),
                    ));
                }
            }
        }
        if old_ledger
            .commits
            .last()
            .map(|commit| commit.commit_hash.as_str())
            == Some(plan.new_commit_hash.as_str())
        {
            let count_before = old_rows.len().saturating_sub(1);
            if old_rows.last() != Some(&bundle_expected_row(&plan)) {
                let _ = connection.execute_batch("ROLLBACK");
                return Err(persistence_bundle_failure(
                    "persistence_bundle_recovery_conflict",
                    "committed bundle row does not match the exact plan",
                    Some("payload"),
                ));
            }
            for ((path, bytes), (sidecar, sidecar_bytes)) in plan
                .artifact_paths
                .iter()
                .zip(&plan.artifact_bytes)
                .zip(plan.sidecar_paths.iter().zip(&plan.sidecar_bytes))
            {
                if !bundle_file_matches(path, bytes) || !bundle_file_matches(sidecar, sidecar_bytes)
                {
                    let _ = connection.execute_batch("ROLLBACK");
                    return Err(persistence_bundle_failure(
                        "persistence_bundle_recovery_conflict",
                        "committed bundle output is invalid",
                        Some("payload.artifacts"),
                    ));
                }
            }
            let _ = connection.execute_batch("ROLLBACK");
            drop(connection);
            if !bundle_snapshot_matches(&run_dir, &initial_snapshot) {
                return Err(PersistenceBundleFailure {
                    status: KernelResponseStatus::Error,
                    code: "persistence_bundle_postcondition_failed",
                    message: "recovery run tree changed before intent cleanup".to_owned(),
                    path: Some("payload"),
                    mutation_performed: true,
                });
            }
            if fault == PersistenceBundleFault::IntentCleanup {
                return Err(PersistenceBundleFailure {
                    status: KernelResponseStatus::Error,
                    code: "persistence_bundle_intent_cleanup_failed",
                    message: "injected recovered intent cleanup fault".to_owned(),
                    path: Some("payload.run_id"),
                    mutation_performed: true,
                });
            }
            fs::remove_file(&intent_path).map_err(|_| PersistenceBundleFailure {
                status: KernelResponseStatus::Error,
                code: "persistence_bundle_intent_cleanup_failed",
                message: "bundle intent cleanup failed".to_owned(),
                path: Some("payload.run_id"),
                mutation_performed: true,
            })?;
            if File::open(&run_dir)
                .and_then(|directory| directory.sync_all())
                .is_err()
            {
                let intent = bundle_intent_value(&plan, &root);
                let _ = write_bundle_intent(&intent_path, &intent, PersistenceBundleFault::None);
                return Err(PersistenceBundleFailure {
                    status: KernelResponseStatus::Error,
                    code: "persistence_bundle_durability_uncertain",
                    message: "bundle intent cleanup durability is uncertain".to_owned(),
                    path: Some("payload.run_id"),
                    mutation_performed: true,
                });
            }
            let mut recovered_snapshot = initial_snapshot.clone();
            recovered_snapshot.remove(
                intent_path
                    .strip_prefix(&run_dir)
                    .expect("intent path beneath run"),
            );
            if fault == PersistenceBundleFault::FinalPostcondition
                || fs::symlink_metadata(&intent_path).is_ok()
                || plan
                    .artifact_paths
                    .iter()
                    .zip(&plan.artifact_bytes)
                    .any(|(path, bytes)| !bundle_file_matches(path, bytes))
                || plan
                    .sidecar_paths
                    .iter()
                    .zip(&plan.sidecar_bytes)
                    .any(|(path, bytes)| !bundle_file_matches(path, bytes))
                || !bundle_snapshot_matches(&run_dir, &recovered_snapshot)
            {
                if fs::symlink_metadata(&intent_path).is_err() {
                    let intent = bundle_intent_value(&plan, &root);
                    if write_bundle_intent(&intent_path, &intent, PersistenceBundleFault::None)
                        .is_err()
                    {
                        return Err(PersistenceBundleFailure {
                            status: KernelResponseStatus::Error,
                            code: "persistence_bundle_durability_uncertain",
                            message: "recovery final check failed and intent could not be restored"
                                .to_owned(),
                            path: Some("payload"),
                            mutation_performed: true,
                        });
                    }
                }
                return Err(PersistenceBundleFailure {
                    status: KernelResponseStatus::Error,
                    code: "persistence_bundle_postcondition_failed",
                    message: "recovered bundle final postcondition failed".to_owned(),
                    path: Some("payload"),
                    mutation_performed: true,
                });
            }
            return Ok(commit_bundle_result(&plan, count_before, true));
        }
    } else if old_ledger
        .commits
        .last()
        .map(|commit| commit.commit_hash.as_str())
        != Some(plan.previous_tip_hash.as_str())
    {
        let _ = connection.execute_batch("ROLLBACK");
        return Err(persistence_bundle_failure(
            "persistence_bundle_tip_mismatch",
            "expected tip does not match persisted ledger",
            Some("payload.expected_tip_hash"),
        ));
    }
    if !intent_exists {
        for (path, sidecar) in plan.artifact_paths.iter().zip(&plan.sidecar_paths) {
            if fs::symlink_metadata(path).is_ok() || fs::symlink_metadata(sidecar).is_ok() {
                let _ = connection.execute_batch("ROLLBACK");
                return Err(persistence_bundle_failure(
                    "persistence_bundle_target_exists",
                    "bundle destination already exists",
                    Some("payload.artifacts"),
                ));
            }
        }
        if !bundle_snapshot_matches(&run_dir, &expected_snapshot) {
            let _ = connection.execute_batch("ROLLBACK");
            return Err(persistence_bundle_failure(
                "persistence_bundle_snapshot_changed",
                "run tree changed before intent publication",
                Some("payload.run_id"),
            ));
        }
        let intent = bundle_intent_value(&plan, &root);
        if let Err(error) = write_bundle_intent(&intent_path, &intent, fault) {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &[],
                BundleRollbackIntentState::Absent,
                error,
            ));
        }
        insert_bundle_snapshot_file(
            &mut expected_snapshot,
            &run_dir,
            &intent_path,
            &expected_intent_bytes,
        );
        if !bundle_snapshot_matches(&run_dir, &expected_snapshot) {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &[],
                BundleRollbackIntentState::Fresh,
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree changed after intent publication",
                    Some("payload.run_id"),
                ),
            ));
        }
    } else if old_ledger
        .commits
        .last()
        .map(|commit| commit.commit_hash.as_str())
        != Some(plan.previous_tip_hash.as_str())
        && old_ledger
            .commits
            .last()
            .map(|commit| commit.commit_hash.as_str())
            != Some(plan.new_commit_hash.as_str())
    {
        let _ = connection.execute_batch("ROLLBACK");
        return Err(persistence_bundle_failure(
            "persistence_bundle_recovery_conflict",
            "persisted ledger tip is neither the intent parent nor the committed bundle",
            Some("payload.expected_tip_hash"),
        ));
    }
    let mut created = Vec::new();
    let rollback_intent_state = if intent_exists {
        BundleRollbackIntentState::Recovery
    } else {
        BundleRollbackIntentState::Fresh
    };
    for (path, bytes) in plan.artifact_paths.iter().zip(&plan.artifact_bytes) {
        if fault == PersistenceBundleFault::SnapshotBeforeArtifact
            || !bundle_snapshot_matches(&run_dir, &expected_snapshot)
        {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &created,
                rollback_intent_state,
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree changed before artifact publication",
                    Some("payload.artifacts"),
                ),
            ));
        }
        match publish_bundle_file(path, bytes, "artifact", intent_exists, fault) {
            Ok(true) => {
                created.push(path.clone());
                insert_bundle_snapshot_file(&mut expected_snapshot, &run_dir, path, bytes);
            }
            Ok(false) => {}
            Err(error) => {
                return Err(rollback_bundle_failure(
                    &connection,
                    &run_dir,
                    &intent_path,
                    &initial_snapshot,
                    &created,
                    rollback_intent_state,
                    error,
                ));
            }
        }
        if fault == PersistenceBundleFault::ArtifactAfterPublish && created.len() == 1 {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &created,
                rollback_intent_state,
                persistence_bundle_failure(
                    "persistence_bundle_publish_failed",
                    "injected artifact post-publication fault",
                    Some("payload.artifacts"),
                ),
            ));
        }
        if !bundle_snapshot_matches(&run_dir, &expected_snapshot) {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &created,
                rollback_intent_state,
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree changed after artifact publication",
                    Some("payload.artifacts"),
                ),
            ));
        }
    }
    for (path, bytes) in plan.sidecar_paths.iter().zip(&plan.sidecar_bytes) {
        if fault == PersistenceBundleFault::SnapshotBeforeSidecar
            || !bundle_snapshot_matches(&run_dir, &expected_snapshot)
        {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &created,
                rollback_intent_state,
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree changed before sidecar publication",
                    Some("payload.artifacts"),
                ),
            ));
        }
        match publish_bundle_file(path, bytes, "sidecar", intent_exists, fault) {
            Ok(true) => {
                created.push(path.clone());
                insert_bundle_snapshot_file(&mut expected_snapshot, &run_dir, path, bytes);
            }
            Ok(false) => {}
            Err(error) => {
                return Err(rollback_bundle_failure(
                    &connection,
                    &run_dir,
                    &intent_path,
                    &initial_snapshot,
                    &created,
                    rollback_intent_state,
                    error,
                ));
            }
        }
        if fault == PersistenceBundleFault::SidecarAfterPublish
            && created.len() == plan.artifact_paths.len() + 1
        {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &created,
                rollback_intent_state,
                persistence_bundle_failure(
                    "persistence_bundle_publish_failed",
                    "injected sidecar post-publication fault",
                    Some("payload.artifacts"),
                ),
            ));
        }
        if !bundle_snapshot_matches(&run_dir, &expected_snapshot) {
            return Err(rollback_bundle_failure(
                &connection,
                &run_dir,
                &intent_path,
                &initial_snapshot,
                &created,
                rollback_intent_state,
                persistence_bundle_failure(
                    "persistence_bundle_snapshot_changed",
                    "run tree changed after sidecar publication",
                    Some("payload.artifacts"),
                ),
            ));
        }
    }
    if fault == PersistenceBundleFault::Rollback {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_rollback_uncertain",
            message: "injected bundle rollback uncertainty".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    let expected_row = bundle_expected_row(&plan);
    if fault == PersistenceBundleFault::Insert {
        return Err(rollback_bundle_failure(
            &connection,
            &run_dir,
            &intent_path,
            &initial_snapshot,
            &created,
            rollback_intent_state,
            persistence_bundle_failure(
                "persistence_bundle_insert_failed",
                "injected bundle insert fault",
                Some("payload"),
            ),
        ));
    }
    let insert = connection.execute(
        "INSERT INTO commits (commit_hash,parent_hash,run_id,candidate_id,action_type,payload_json,artifact_refs_json,timestamp) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
        params![expected_row.commit_hash, expected_row.parent_hash, expected_row.run_id, expected_row.candidate_id, expected_row.action_type, expected_row.payload_json, expected_row.artifact_refs_json, expected_row.timestamp],
    );
    if insert.is_err() {
        return Err(rollback_bundle_failure(
            &connection,
            &run_dir,
            &intent_path,
            &initial_snapshot,
            &created,
            rollback_intent_state,
            persistence_bundle_failure(
                "persistence_bundle_insert_failed",
                "bundle ledger insert failed",
                Some("payload"),
            ),
        ));
    }
    let inserted_row = connection.query_row(
        "SELECT commit_hash,parent_hash,run_id,candidate_id,action_type,payload_json,artifact_refs_json,timestamp FROM commits WHERE commit_hash=?1",
        [&plan.new_commit_hash],
        |row| {
            Ok(RawLedgerRow {
                commit_hash: row.get(0)?,
                parent_hash: row.get(1)?,
                run_id: row.get(2)?,
                candidate_id: row.get(3)?,
                action_type: row.get(4)?,
                payload_json: row.get(5)?,
                artifact_refs_json: row.get(6)?,
                timestamp: row.get(7)?,
            })
        },
    );
    if inserted_row.as_ref().ok() != Some(&expected_row) {
        return Err(rollback_bundle_failure(
            &connection,
            &run_dir,
            &intent_path,
            &initial_snapshot,
            &created,
            rollback_intent_state,
            persistence_bundle_failure(
                "persistence_bundle_insert_failed",
                "bundle ledger row did not round-trip exactly",
                Some("payload"),
            ),
        ));
    }
    if fault == PersistenceBundleFault::Readback {
        return Err(rollback_bundle_failure(
            &connection,
            &run_dir,
            &intent_path,
            &initial_snapshot,
            &created,
            rollback_intent_state,
            persistence_bundle_failure(
                "persistence_bundle_insert_failed",
                "injected bundle row readback fault",
                Some("payload"),
            ),
        ));
    }
    if connection.execute_batch("COMMIT").is_err() {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_commit_uncertain",
            message: "bundle commit outcome is uncertain".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    if fault == PersistenceBundleFault::Commit {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_commit_uncertain",
            message: "injected bundle commit uncertainty".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    drop(connection);
    let mut committed_snapshot = expected_snapshot.clone();
    let committed_ledger_bytes = fs::read(&ledger_path).map_err(|_| PersistenceBundleFailure {
        status: KernelResponseStatus::Error,
        code: "persistence_bundle_postcondition_failed",
        message: "committed ledger bytes cannot be read".to_owned(),
        path: Some("payload.run_id"),
        mutation_performed: true,
    })?;
    insert_bundle_snapshot_file(
        &mut committed_snapshot,
        &run_dir,
        &ledger_path,
        &committed_ledger_bytes,
    );
    if !bundle_snapshot_matches(&run_dir, &committed_snapshot) {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message: "committed run tree contains a changed or unexpected path".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    if fault == PersistenceBundleFault::Reopen {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message: "injected bundle reopen fault".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    let reopened = Connection::open_with_flags(
        &ledger_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|_| PersistenceBundleFailure {
        status: KernelResponseStatus::Error,
        code: "persistence_bundle_postcondition_failed",
        message: "bundle ledger cannot be reopened".to_owned(),
        path: Some("payload"),
        mutation_performed: true,
    })?;
    let journal_mode_after: String = reopened
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .map_err(|_| PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message: "ledger journal mode cannot be rechecked".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        })?;
    if journal_mode_after != journal_mode_before {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message: "ledger journal mode changed".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    let (new_rows, new_ledger) = validate_exact_read_only_ledger(&reopened, &request.run_id)
        .map_err(|message| PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message,
            path: Some("payload"),
            mutation_performed: true,
        })?;
    if new_rows.len() != old_rows.len() + 1
        || new_rows[..old_rows.len()] != old_rows
        || new_rows.last() != Some(&expected_row)
        || new_ledger
            .commits
            .last()
            .map(|commit| commit.commit_hash.as_str())
            != Some(plan.new_commit_hash.as_str())
    {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message: "bundle ledger postcondition failed".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    for ((path, bytes), (sidecar, sidecar_bytes)) in plan
        .artifact_paths
        .iter()
        .zip(&plan.artifact_bytes)
        .zip(plan.sidecar_paths.iter().zip(&plan.sidecar_bytes))
    {
        if !bundle_file_matches(path, bytes) || !bundle_file_matches(sidecar, sidecar_bytes) {
            return Err(PersistenceBundleFailure {
                status: KernelResponseStatus::Error,
                code: "persistence_bundle_postcondition_failed",
                message: "bundle output postcondition failed".to_owned(),
                path: Some("payload.artifacts"),
                mutation_performed: true,
            });
        }
    }
    if fault == PersistenceBundleFault::IntentCleanup {
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_intent_cleanup_failed",
            message: "injected bundle intent cleanup fault".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    fs::remove_file(&intent_path).map_err(|_| PersistenceBundleFailure {
        status: KernelResponseStatus::Error,
        code: "persistence_bundle_intent_cleanup_failed",
        message: "bundle intent cleanup failed".to_owned(),
        path: Some("payload.run_id"),
        mutation_performed: true,
    })?;
    if File::open(&run_dir)
        .and_then(|directory| directory.sync_all())
        .is_err()
    {
        let intent = bundle_intent_value(&plan, &root);
        let _ = write_bundle_intent(&intent_path, &intent, PersistenceBundleFault::None);
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_durability_uncertain",
            message: "bundle intent cleanup durability is uncertain".to_owned(),
            path: Some("payload.run_id"),
            mutation_performed: true,
        });
    }
    committed_snapshot.remove(
        intent_path
            .strip_prefix(&run_dir)
            .expect("intent path beneath run"),
    );
    if fault == PersistenceBundleFault::FinalPostcondition
        || !bundle_snapshot_matches(&run_dir, &committed_snapshot)
    {
        if fs::symlink_metadata(&intent_path).is_err() {
            let intent = bundle_intent_value(&plan, &root);
            if write_bundle_intent(&intent_path, &intent, PersistenceBundleFault::None).is_err() {
                return Err(PersistenceBundleFailure {
                    status: KernelResponseStatus::Error,
                    code: "persistence_bundle_durability_uncertain",
                    message: "bundle final check failed and intent could not be restored"
                        .to_owned(),
                    path: Some("payload"),
                    mutation_performed: true,
                });
            }
        }
        return Err(PersistenceBundleFailure {
            status: KernelResponseStatus::Error,
            code: "persistence_bundle_postcondition_failed",
            message: "bundle final run-tree postcondition failed".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    Ok(commit_bundle_result(&plan, old_rows.len(), recovered))
}

fn artifact_persist_failure(
    code: &'static str,
    message: impl Into<String>,
    path: Option<&'static str>,
) -> ArtifactPersistFailure {
    ArtifactPersistFailure {
        status: KernelResponseStatus::Rejected,
        code,
        message: message.into(),
        path,
        mutation_performed: false,
    }
}

fn persist_artifact_payload(
    payload: &Map<String, Value>,
    project_root: &Path,
) -> Result<(Map<String, Value>, Vec<KernelDiagnostic>), ArtifactPersistFailure> {
    persist_artifact_payload_with_fault(payload, project_root, ArtifactPersistFault::None)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
enum ArtifactPersistFault {
    None,
    TempCreate,
    TempWrite,
    TempFlush,
    TempFsync,
    Publish,
    TempCleanup,
    DirectoryFsync,
    Postcondition,
}

fn persist_artifact_payload_with_fault(
    payload: &Map<String, Value>,
    project_root: &Path,
    fault: ArtifactPersistFault,
) -> Result<(Map<String, Value>, Vec<KernelDiagnostic>), ArtifactPersistFailure> {
    let request = serde_json::from_value::<ArtifactPersistPayload>(Value::Object(payload.clone()))
        .map_err(|error| {
            artifact_persist_failure(
                "artifact_persist_payload_invalid",
                error.to_string(),
                Some("payload"),
            )
        })?;
    validate_artifact_persist_payload_structure(payload).map_err(|message| {
        let code = if message.contains("12 MiB") || message.contains("64 KiB") {
            "artifact_persist_size_exceeded"
        } else {
            "artifact_persist_payload_invalid"
        };
        artifact_persist_failure(code, message, Some("payload"))
    })?;
    let root_meta = fs::symlink_metadata(project_root).map_err(|_| {
        artifact_persist_failure(
            "artifact_persist_run_missing",
            "project root is unavailable",
            Some("payload.run_id"),
        )
    })?;
    if !root_meta.is_dir() || root_meta.file_type().is_symlink() {
        return Err(artifact_persist_failure(
            "artifact_persist_directory_invalid",
            "project root is not a real directory",
            Some("payload.run_id"),
        ));
    }
    let root = fs::canonicalize(project_root).map_err(|_| {
        artifact_persist_failure(
            "artifact_persist_run_missing",
            "project root is unavailable",
            Some("payload.run_id"),
        )
    })?;
    let runs_root = root.join("runs");
    let run_dir = runs_root.join(&request.run_id);
    let type_dir_name = artifact_directory(&request.artifact_type).expect("validated type");
    let type_dir = run_dir.join(type_dir_name);
    for (index, directory) in [&runs_root, &run_dir, &type_dir].into_iter().enumerate() {
        let metadata = fs::symlink_metadata(directory).map_err(|_| {
            artifact_persist_failure(
                if index < 2 {
                    "artifact_persist_run_missing"
                } else {
                    "artifact_persist_directory_invalid"
                },
                "required persistence directory is missing",
                Some("payload.run_id"),
            )
        })?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err(artifact_persist_failure(
                "artifact_persist_directory_invalid",
                "required run directory is not a real directory",
                Some("payload.run_id"),
            ));
        }
    }
    let stem = request
        .filename_stem_optional
        .as_deref()
        .unwrap_or(&request.artifact_id);
    let relative = format!("runs/{}/{}/{}.json", request.run_id, type_dir_name, stem);
    let destination = type_dir.join(format!("{stem}.json"));
    let sidecar = destination.with_extension("json.meta.json");
    for path in [&destination, &sidecar] {
        if fs::symlink_metadata(path).is_ok() {
            return Err(artifact_persist_failure(
                "artifact_persist_target_exists",
                "artifact destination or sidecar already exists",
                Some("payload"),
            ));
        }
    }
    let canonical = canonical_json(&request.json_value).map_err(|error| {
        artifact_persist_failure(
            "artifact_persist_payload_invalid",
            error.to_string(),
            Some("payload.json_value"),
        )
    })?;
    let bytes = format!("{canonical}\n").into_bytes();
    let mut metadata = request.metadata;
    metadata.insert("format".to_owned(), Value::String("json".to_owned()));
    metadata.insert("is_verification_evidence".to_owned(), Value::Bool(false));
    static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);
    let nonce = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    let temp = type_dir.join(format!(
        ".{}.tmp-{}-{}",
        stem,
        std::process::id(),
        now + nonce as u128
    ));
    if fault == ArtifactPersistFault::TempCreate {
        return Err(artifact_persist_failure(
            "artifact_persist_temp_write_failed",
            "injected temporary file create failure",
            Some("payload"),
        ));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|error| {
            artifact_persist_failure(
                "artifact_persist_temp_write_failed",
                format!("temporary file create failed: {error}"),
                Some("payload"),
            )
        })?;
    let write_result = if fault == ArtifactPersistFault::TempWrite {
        Err(std::io::Error::other("injected temporary write failure"))
    } else {
        file.write_all(&bytes)
    }
    .and_then(|_| {
        if fault == ArtifactPersistFault::TempFlush {
            Err(std::io::Error::other("injected temporary flush failure"))
        } else {
            file.flush()
        }
    })
    .and_then(|_| {
        if fault == ArtifactPersistFault::TempFsync {
            Err(std::io::Error::other("injected temporary fsync failure"))
        } else {
            file.sync_all()
        }
    });
    if let Err(error) = write_result {
        let _ = fs::remove_file(&temp);
        return Err(artifact_persist_failure(
            "artifact_persist_temp_write_failed",
            format!("temporary file write failed: {error}"),
            Some("payload"),
        ));
    }
    drop(file);
    if fault == ArtifactPersistFault::Publish {
        let _ = fs::remove_file(&temp);
        return Err(artifact_persist_failure(
            "artifact_persist_publish_failed",
            "injected atomic publish failure",
            Some("payload"),
        ));
    }
    if let Err(error) = fs::hard_link(&temp, &destination) {
        let _ = fs::remove_file(&temp);
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            return Err(artifact_persist_failure(
                "artifact_persist_target_exists",
                "artifact destination was created concurrently",
                Some("payload"),
            ));
        }
        return Err(artifact_persist_failure(
            "artifact_persist_publish_failed",
            format!("atomic no-clobber publish failed: {error}"),
            Some("payload"),
        ));
    }
    let mut diagnostics = Vec::new();
    if fault == ArtifactPersistFault::TempCleanup || fs::remove_file(&temp).is_err() {
        diagnostics.push(KernelDiagnostic {
            code: "artifact_persist_temp_cleanup_warning".to_owned(),
            message: "temporary file cleanup failed after publication".to_owned(),
            path: Some("payload".to_owned()),
        });
    }
    let directory_sync = if fault == ArtifactPersistFault::DirectoryFsync {
        Err(std::io::Error::other(
            "injected containing directory fsync failure",
        ))
    } else {
        File::open(&type_dir).and_then(|directory| directory.sync_all())
    };
    match directory_sync {
        Ok(()) => {}
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::Unsupported | std::io::ErrorKind::InvalidInput
            ) => {}
        Err(error) => {
            return Err(ArtifactPersistFailure {
                status: KernelResponseStatus::Error,
                code: "artifact_persist_durability_uncertain",
                message: format!("containing directory durability is uncertain: {error}"),
                path: Some("payload"),
                mutation_performed: true,
            })
        }
    }
    if fault == ArtifactPersistFault::Postcondition {
        return Err(ArtifactPersistFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_persist_postcondition_failed",
            message: "injected postcondition failure".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    let final_meta =
        fs::symlink_metadata(&destination).map_err(|error| ArtifactPersistFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_persist_postcondition_failed",
            message: format!("published artifact cannot be stat'ed: {error}"),
            path: Some("payload"),
            mutation_performed: true,
        })?;
    if !final_meta.is_file() || final_meta.file_type().is_symlink() {
        return Err(ArtifactPersistFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_persist_postcondition_failed",
            message: "published artifact is not a regular file".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    let observed_len = final_meta.len() as usize;
    let observed_hash = sha256_file(&destination).map_err(|message| ArtifactPersistFailure {
        status: KernelResponseStatus::Error,
        code: "artifact_persist_postcondition_failed",
        message,
        path: Some("payload"),
        mutation_performed: true,
    })?;
    let expected_hash = sha256_hex(&bytes);
    if observed_len != bytes.len() || observed_hash != expected_hash {
        return Err(ArtifactPersistFailure {
            status: KernelResponseStatus::Error,
            code: "artifact_persist_postcondition_failed",
            message: "published artifact bytes do not match request".to_owned(),
            path: Some("payload"),
            mutation_performed: true,
        });
    }
    let artifact = map_value([
        ("id", Value::String(request.artifact_id)),
        ("type", Value::String(request.artifact_type)),
        ("path", Value::String(relative)),
        ("content_hash", Value::String(expected_hash)),
        ("producing_commit_hash", Value::Null),
        ("metadata", Value::Object(metadata)),
    ]);
    Ok((
        map_value([
            ("artifact", Value::Object(artifact)),
            (
                "bytes_written",
                Value::Number(serde_json::Number::from(bytes.len())),
            ),
            ("created", Value::Bool(true)),
            ("linked_to_ledger", Value::Bool(false)),
            ("authority_granted", Value::Bool(false)),
        ]),
        diagnostics,
    ))
}

fn validate_checkpoint_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    if payload.len() != 2 || !payload.contains_key("run_id") || !payload.contains_key("index") {
        return Err(
            "checkpoint.verify payload must contain exactly run_id and index fields".to_owned(),
        );
    }
    let request = serde_json::from_value::<CheckpointVerifyPayload>(Value::Object(payload.clone()))
        .map_err(|error| error.to_string())?;
    if !is_safe_segment(&request.run_id) {
        return Err("checkpoint run_id has invalid identifier syntax".to_owned());
    }
    if !is_safe_segment(&request.index.artifact_id) {
        return Err("checkpoint index artifact_id has invalid identifier syntax".to_owned());
    }
    if !is_sha256_hex(&request.index.producing_commit_hash) {
        return Err(
            "checkpoint index producing_commit_hash must be a lowercase SHA-256 digest".to_owned(),
        );
    }
    Ok(())
}

fn validate_replay_core_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    if payload.len() != 2
        || !payload.contains_key("run_id")
        || !payload.contains_key("ledger_tip_hash")
    {
        return Err(
            "replay.verify_core payload must contain exactly run_id and ledger_tip_hash fields"
                .to_owned(),
        );
    }
    let request = serde_json::from_value::<ReplayVerifyCorePayload>(Value::Object(payload.clone()))
        .map_err(|error| error.to_string())?;
    if !is_safe_segment(&request.run_id) {
        return Err("replay run_id has invalid identifier syntax".to_owned());
    }
    if !is_sha256_hex(&request.ledger_tip_hash) {
        return Err("ledger_tip_hash must be a lowercase SHA-256 digest".to_owned());
    }
    Ok(())
}

fn validate_transport_request(request: &KernelRequest) -> Result<(), String> {
    if request.operation != KernelOperation::EvidenceValidateBundle {
        return validate_kernel_request(request);
    }
    if request.protocol_version.is_empty() {
        return Err("protocol_version must not be empty".to_owned());
    }
    if request.request_id.is_empty() {
        return Err("request_id must not be empty".to_owned());
    }
    serde_json::from_value::<EvidenceValidateBundlePayload>(Value::Object(request.payload.clone()))
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn validate_artifact_payload_shape(payload: &Map<String, Value>) -> Result<(), String> {
    if payload.len() != 2 || !payload.contains_key("run_id") || !payload.contains_key("artifact") {
        return Err(
            "artifact.verify payload must contain exactly run_id and artifact fields".to_owned(),
        );
    }
    let run_id = payload
        .get("run_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "run_id must be a string".to_owned())?;
    if run_id.is_empty() {
        return Err("run_id must not be empty".to_owned());
    }
    if !payload.get("artifact").is_some_and(Value::is_object) {
        return Err("artifact must be an object".to_owned());
    }
    Ok(())
}

fn validate_artifact_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    validate_artifact_payload_shape(payload)?;
    validate_artifact_ref(payload.get("artifact").expect("checked above"))?;
    serde_json::from_value::<ArtifactVerifyPayload>(Value::Object(payload.clone()))
        .map_err(|error| error.to_string())?;
    Ok(())
}

type BundleValidationError = (&'static str, String, Option<&'static str>);

fn validate_evidence_bundle_payload_structure(
    payload: &Map<String, Value>,
) -> Result<(), BundleValidationError> {
    let bundle: EvidenceValidateBundlePayload =
        serde_json::from_value(Value::Object(payload.clone()))
            .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;
    if !is_sha256_hex(&bundle.producing_commit_hash) {
        return Err((
            "protocol_invalid",
            "producing_commit_hash must be a lowercase SHA-256 hex digest".to_owned(),
            Some("payload.producing_commit_hash"),
        ));
    }
    if !is_safe_segment(&bundle.run_id)
        || !is_safe_segment(&bundle.candidate_id)
        || !is_safe_segment(&bundle.claim_id)
    {
        return Err((
            "protocol_invalid",
            "bundle identifiers contain unsafe characters".to_owned(),
            Some("payload"),
        ));
    }
    let members = bundle_member_ids(&bundle.bundle);
    if members.iter().any(|member| !is_safe_segment(member)) {
        return Err((
            "protocol_invalid",
            "bundle artifact ids contain unsafe characters".to_owned(),
            Some("payload.bundle"),
        ));
    }
    if members.iter().collect::<HashSet<_>>().len() != members.len() {
        return Err((
            "bundle_member_duplicate",
            "bundle artifact ids must be distinct".to_owned(),
            Some("payload.bundle"),
        ));
    }
    Ok(())
}

fn bundle_member_ids(bundle: &EvidenceBundle) -> Vec<&str> {
    match bundle {
        EvidenceBundle::Lean {
            contract_artifact_id,
            payload_artifact_id,
            trace_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        } => vec![
            contract_artifact_id,
            payload_artifact_id,
            trace_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        ],
        EvidenceBundle::Synthetic {
            contract_artifact_id,
            input_artifact_id,
            trace_artifact_id,
            output_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        } => vec![
            contract_artifact_id,
            input_artifact_id,
            trace_artifact_id,
            output_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        ],
    }
}

fn validate_evidence_bundle_payload(
    payload: &Map<String, Value>,
    project_root: Option<&Path>,
) -> Result<Map<String, Value>, (&'static str, String, Option<&'static str>)> {
    validate_evidence_bundle_payload_structure(payload)?;
    let request: EvidenceValidateBundlePayload =
        serde_json::from_value(Value::Object(payload.clone()))
            .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;
    let root = project_root.ok_or((
        "kernel_root_missing",
        "evidence bundle validation requires --root <project-root>".to_owned(),
        None,
    ))?;
    let ledger = load_persisted_ledger(root, &request.run_id)?;
    verify_ledger(&ledger)?;
    let commit = ledger
        .commits
        .iter()
        .find(|commit| commit.commit_hash == request.producing_commit_hash)
        .ok_or((
            "bundle_commit_mismatch",
            "producing commit is absent from the persisted run ledger".to_owned(),
            Some("payload.producing_commit_hash"),
        ))?;
    let (action, expected_type) = match &request.bundle {
        EvidenceBundle::Lean { .. } => ("StageCProofValidated", "lean"),
        EvidenceBundle::Synthetic { .. } => ("StageCSyntheticExperimentRun", "experiment"),
    };
    if commit.run_id != request.run_id {
        return Err((
            "bundle_commit_mismatch",
            "producing commit belongs to a different run".to_owned(),
            Some("payload.producing_commit_hash"),
        ));
    }
    if commit.candidate_id.as_deref() != Some(request.candidate_id.as_str()) {
        return Err((
            "bundle_candidate_mismatch",
            "producing commit candidate does not match the request".to_owned(),
            Some("payload.candidate_id"),
        ));
    }
    if commit.action_type != action {
        return Err((
            "bundle_commit_mismatch",
            format!("producing commit action must be {action}"),
            Some("payload.producing_commit_hash"),
        ));
    }
    let member_ids = bundle_member_ids(&request.bundle);
    let commit_ids: HashSet<&str> = commit
        .artifact_refs
        .iter()
        .filter_map(|value| value.as_object()?.get("id")?.as_str())
        .collect();
    let requested_ids: HashSet<&str> = member_ids.iter().copied().collect();
    if commit_ids != requested_ids {
        return Err((
            "bundle_member_unexpected",
            "producing commit artifact set does not exactly match the bundle".to_owned(),
            Some("payload.bundle"),
        ));
    }

    let mut members = HashMap::new();
    for member_id in member_ids {
        let reference = commit
            .artifact_refs
            .iter()
            .find(|value| {
                value
                    .as_object()
                    .and_then(|object| object.get("id"))
                    .and_then(Value::as_str)
                    == Some(member_id)
            })
            .ok_or((
                "bundle_member_missing",
                format!("bundle member is not in producing commit: {member_id}"),
                Some("payload.bundle"),
            ))?;
        let artifact: WireArtifactRef =
            serde_json::from_value(reference.clone()).map_err(|error| {
                (
                    "bundle_member_unexpected",
                    format!("bundle member reference is invalid: {error}"),
                    Some("payload.bundle"),
                )
            })?;
        if artifact.artifact_type != expected_type {
            return Err((
                "bundle_member_unexpected",
                format!("bundle member has unexpected artifact type: {member_id}"),
                Some("payload.bundle"),
            ));
        }
        let content = read_bundle_artifact(root, &request.run_id, commit, &artifact, member_id)?;
        members.insert(member_id.to_owned(), (artifact, content));
    }

    match &request.bundle {
        EvidenceBundle::Lean {
            contract_artifact_id,
            payload_artifact_id,
            trace_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        } => validate_lean_bundle(
            &request,
            commit,
            &members,
            contract_artifact_id,
            payload_artifact_id,
            trace_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        )?,
        EvidenceBundle::Synthetic {
            contract_artifact_id,
            input_artifact_id,
            trace_artifact_id,
            output_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        } => validate_synthetic_bundle(
            &request,
            commit,
            &members,
            contract_artifact_id,
            input_artifact_id,
            trace_artifact_id,
            output_artifact_id,
            result_artifact_id,
            safety_artifact_id,
        )?,
    }
    let ids = member_ids_for_result(&request.bundle);
    Ok(map_value([
        ("run_id", Value::String(request.run_id)),
        ("candidate_id", Value::String(request.candidate_id)),
        ("claim_id", Value::String(request.claim_id)),
        (
            "bundle_kind",
            Value::String(match request.bundle {
                EvidenceBundle::Lean { .. } => "LeanProof".to_owned(),
                EvidenceBundle::Synthetic { .. } => "SyntheticExperiment".to_owned(),
            }),
        ),
        (
            "producing_commit_hash",
            Value::String(request.producing_commit_hash),
        ),
        (
            "validated_artifact_ids",
            Value::Array(ids.into_iter().map(Value::String).collect()),
        ),
        ("bundle_valid", Value::Bool(true)),
        ("authority_granted", Value::Bool(false)),
    ]))
}

fn member_ids_for_result(bundle: &EvidenceBundle) -> Vec<String> {
    bundle_member_ids(bundle)
        .into_iter()
        .map(str::to_owned)
        .collect()
}

fn read_bundle_artifact(
    root: &Path,
    run_id: &str,
    commit: &WireLedgerCommit,
    artifact: &WireArtifactRef,
    member_id: &str,
) -> Result<Value, (&'static str, String, Option<&'static str>)> {
    if artifact.producing_commit_hash.as_deref() != Some(commit.commit_hash.as_str()) {
        return Err((
            "bundle_commit_mismatch",
            format!("bundle member has an incorrect producer: {member_id}"),
            Some("payload.bundle"),
        ));
    }
    validate_artifact_location(run_id, artifact)
        .map_err(|message| ("path_invalid", message, Some("payload.bundle")))?;
    let path = resolve_run_file(root, run_id, &artifact.path)
        .map_err(|message| ("path_invalid", message, Some("payload.bundle")))?;
    let bytes = fs::read(&path).map_err(|error| {
        (
            "artifact_missing",
            format!("bundle member cannot be read: {error}"),
            Some("payload.bundle"),
        )
    })?;
    let actual_hash = sha256_hex(&bytes);
    if actual_hash != artifact.content_hash {
        return Err((
            "hash_mismatch",
            format!(
                "bundle member bytes hash to {actual_hash}, expected {}",
                artifact.content_hash
            ),
            Some("payload.bundle"),
        ));
    }
    let value = parse_json_without_duplicate_keys(std::str::from_utf8(&bytes).map_err(|_| {
        (
            "protocol_invalid",
            "bundle member must contain UTF-8 JSON".to_owned(),
            Some("payload.bundle"),
        )
    })?)
    .map_err(|error| {
        (
            "protocol_invalid",
            format!("bundle member JSON is invalid: {error}"),
            Some("payload.bundle"),
        )
    })?;
    if !value.is_object() {
        return Err((
            "protocol_invalid",
            "bundle member JSON must be an object".to_owned(),
            Some("payload.bundle"),
        ));
    }
    Ok(value)
}

fn exact_metadata(
    stage: &str,
    backend: &str,
    provider: &str,
    evidence_role: Option<&str>,
) -> Map<String, Value> {
    let mut metadata = map_value([
        ("format", Value::String("json".to_owned())),
        ("stage", Value::String(stage.to_owned())),
        ("backend", Value::String(backend.to_owned())),
        ("provider", Value::String(provider.to_owned())),
        (
            "is_verification_evidence",
            Value::Bool(evidence_role.is_some()),
        ),
        ("fake", Value::Bool(false)),
    ]);
    if let Some(role) = evidence_role {
        metadata.insert("evidence_role".to_owned(), Value::String(role.to_owned()));
    }
    metadata
}

fn require_metadata(
    artifact: &WireArtifactRef,
    expected: &Map<String, Value>,
) -> Result<(), (&'static str, String, Option<&'static str>)> {
    if artifact.metadata != *expected {
        if artifact.metadata.get("fake") == Some(&Value::Bool(true)) {
            return Err((
                "fake_backend_denied",
                format!("fake bundle member is not admissible: {}", artifact.id),
                Some("payload.bundle"),
            ));
        }
        let backend_mismatch = artifact.metadata.get("backend") != expected.get("backend")
            || artifact.metadata.get("provider") != expected.get("provider");
        return Err((
            if backend_mismatch {
                "bundle_backend_denied"
            } else {
                "authority_denied"
            },
            format!(
                "bundle member metadata is not an exact Stage C metadata map: {}",
                artifact.id
            ),
            Some("payload.bundle"),
        ));
    }
    Ok(())
}

fn bundle_member<'a>(
    members: &'a HashMap<String, (WireArtifactRef, Value)>,
    id: &str,
) -> Result<&'a (WireArtifactRef, Value), (&'static str, String, Option<&'static str>)> {
    members.get(id).ok_or((
        "bundle_member_missing",
        format!("bundle member is absent: {id}"),
        Some("payload.bundle"),
    ))
}

#[allow(clippy::too_many_arguments)]
fn validate_lean_bundle(
    request: &EvidenceValidateBundlePayload,
    commit: &WireLedgerCommit,
    members: &HashMap<String, (WireArtifactRef, Value)>,
    contract_id: &str,
    payload_id: &str,
    trace_id: &str,
    result_id: &str,
    safety_id: &str,
) -> Result<(), (&'static str, String, Option<&'static str>)> {
    let (contract_ref, contract_value) = bundle_member(members, contract_id)?;
    let (payload_ref, payload_value) = bundle_member(members, payload_id)?;
    let (trace_ref, trace_value) = bundle_member(members, trace_id)?;
    let (result_ref, result_value) = bundle_member(members, result_id)?;
    let (safety_ref, safety_value) = bundle_member(members, safety_id)?;
    let contract: ProofContractWire = parse_closed(contract_value, "payload.contract")?;
    let proof_payload: ProofPayloadWire = parse_closed(payload_value, "payload.proof_payload")?;
    let trace: ProofTraceWire = parse_closed(trace_value, "payload.trace")?;
    let result: ProofResultWire = parse_closed(result_value, "payload.result")?;
    let safety: ProofSafetyWire = parse_closed(safety_value, "payload.safety")?;
    if contract.fake_default
        || trace.fake
        || result.fake
        || safety.fake
        || is_fake_backend(&contract.backend)
        || is_fake_backend(&trace.backend)
        || is_fake_backend(&trace.provider)
        || is_fake_backend(&result.backend)
        || is_fake_backend(&result.provider)
    {
        return Err((
            "fake_backend_denied",
            "fake Lean bundle members are not admissible".to_owned(),
            Some("payload.bundle"),
        ));
    }
    if contract.candidate_id != request.candidate_id {
        return Err((
            "bundle_candidate_mismatch",
            "Lean contract candidate does not match the request".to_owned(),
            Some("payload.contract.candidate_id"),
        ));
    }
    if contract.claim_id != request.claim_id {
        return Err((
            "bundle_claim_mismatch",
            "Lean contract claim does not match the request".to_owned(),
            Some("payload.contract.claim_id"),
        ));
    }
    if contract.backend != "lean" {
        return Err((
            "bundle_backend_denied",
            "Lean contract backend is not authorized".to_owned(),
            Some("payload.contract.backend"),
        ));
    }
    require_metadata(
        contract_ref,
        &exact_metadata("stage_c", "lean", "lean", None),
    )?;
    require_metadata(
        payload_ref,
        &exact_metadata("stage_c", "lean", "lean", None),
    )?;
    require_metadata(
        trace_ref,
        &exact_metadata("stage_c", "lean", "lean", Some("proof")),
    )?;
    require_metadata(
        result_ref,
        &exact_metadata("stage_c", "lean", "lean", Some("proof")),
    )?;
    require_metadata(safety_ref, &exact_metadata("stage_c", "lean", "lean", None))?;
    if contract.candidate_id != request.candidate_id
        || contract.claim_id != request.claim_id
        || contract.backend != "lean"
        || contract.proof_language.to_lowercase() != "lean"
        || contract.claim_text.trim().is_empty()
        || contract
            .proof_payload_text
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || contract.proof_payload_path.is_some()
        || contract.proof_payload.len() != 1
        || contract.proof_payload.get("text").and_then(Value::as_str)
            != contract.proof_payload_text.as_deref()
        || contract
            .tool_name
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || contract.expected_output_type != "proof_transcript"
        || !allowed_imports_are_safe(&contract.allowed_imports)
        || !imports_are_allowed(
            contract.proof_payload_text.as_deref().unwrap_or_default(),
            &contract.allowed_imports,
        )
        || contract.timeout_seconds < 1
        || contract.timeout_seconds > 60
        || !contract.allow_external_tools
        || contract.allow_external_calls
        || contract.fake_default
        || contract.is_verification_evidence
    {
        return Err((
            "bundle_contract_invalid",
            "Lean contract violates strict bundle requirements".to_owned(),
            Some("payload.contract"),
        ));
    }
    let contract_text = contract.proof_payload_text.as_deref().unwrap_or_default();
    if proof_payload.candidate_id != contract.candidate_id
        || proof_payload.claim_id != contract.claim_id
        || proof_payload.proof_language != contract.proof_language
        || proof_payload.proof_payload_text != contract_text
        || proof_payload.is_verification_evidence
        || contains_forbidden_proof_token(
            &contract.claim_text,
            contract_text,
            &contract.forbidden_tokens,
        )
        || contains_network_marker(contract_text)
    {
        return Err((
            "bundle_payload_hash_mismatch",
            "Lean payload does not exactly match its contract or safety rules".to_owned(),
            Some("payload.proof_payload"),
        ));
    }
    if result.candidate_id != request.candidate_id
        || result.claim_id != request.claim_id
        || result.backend != "lean"
        || result.provider != "lean"
        || result.proof_language != contract.proof_language
        || result.tool_name != contract.tool_name.clone().unwrap_or_default()
        || result.exit_code != 0
        || !result.verified
        || result.reason.trim().is_empty()
        || result.forbidden_tokens_present
        || result.label != "LeanVerified"
        || result.fake
        || result.raw_trace_artifact_id.as_deref() != Some(trace_id)
        || result.safety_report_artifact_id.as_deref() != Some(safety_id)
        || !is_sha256_hex(&result.stdout_hash)
        || !is_sha256_hex(&result.stderr_hash)
        || !is_sha256_hex(&result.proof_payload_hash)
    {
        return Err((
            "bundle_result_invalid",
            "Lean result does not satisfy strict verification requirements".to_owned(),
            Some("payload.result"),
        ));
    }
    if sha256_hex(contract_text.as_bytes()) != result.proof_payload_hash {
        return Err((
            "bundle_payload_hash_mismatch",
            "Lean proof payload hash does not match the persisted result".to_owned(),
            Some("payload.proof_payload"),
        ));
    }
    if trace.backend != result.backend
        || trace.provider != result.provider
        || trace.tool_name != result.tool_name
        || trace.tool_version != result.tool_version
        || trace.exit_code != result.exit_code
        || result.elapsed_ms != Some(trace.elapsed_ms)
        || trace.fake
        || trace.is_verification_evidence
        || sha256_hex(trace.stdout.as_bytes()) != result.stdout_hash
        || sha256_hex(trace.stderr.as_bytes()) != result.stderr_hash
    {
        return Err((
            "bundle_trace_hash_mismatch",
            "Lean trace does not match the persisted result hashes".to_owned(),
            Some("payload.trace"),
        ));
    }
    if safety.candidate_id != request.candidate_id
        || safety.claim_id != request.claim_id
        || !safety.contract_valid
        || !safety.result_valid
        || !safety.contract_reasons.is_empty()
        || !safety.result_reasons.is_empty()
        || safety.is_verification_evidence
        || safety.fake
    {
        return Err((
            "bundle_safety_invalid",
            "Lean safety report is not a clean validation record".to_owned(),
            Some("payload.safety"),
        ));
    }
    require_commit_payload(
        commit,
        result_value.as_object().expect("closed object schema"),
        &[
            ("proof_backend", "backend"),
            ("proof_provider", "provider"),
            ("proof_contract_id", "contract_artifact_id"),
            ("proof_result_id", "result_artifact_id"),
        ],
        contract_id,
        result_id,
    )?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn validate_synthetic_bundle(
    request: &EvidenceValidateBundlePayload,
    commit: &WireLedgerCommit,
    members: &HashMap<String, (WireArtifactRef, Value)>,
    contract_id: &str,
    input_id: &str,
    trace_id: &str,
    output_id: &str,
    result_id: &str,
    safety_id: &str,
) -> Result<(), (&'static str, String, Option<&'static str>)> {
    let (contract_ref, contract_value) = bundle_member(members, contract_id)?;
    let (input_ref, input_value) = bundle_member(members, input_id)?;
    let (trace_ref, trace_value) = bundle_member(members, trace_id)?;
    let (output_ref, output_value) = bundle_member(members, output_id)?;
    let (result_ref, result_value) = bundle_member(members, result_id)?;
    let (safety_ref, safety_value) = bundle_member(members, safety_id)?;
    let contract: SyntheticContractWire = parse_closed(contract_value, "payload.contract")?;
    let input: SyntheticInputWire = parse_closed(input_value, "payload.input")?;
    let trace: SyntheticTraceWire = parse_closed(trace_value, "payload.trace")?;
    let output: SyntheticOutputWire = parse_closed(output_value, "payload.output")?;
    let result: SyntheticResultWire = parse_closed(result_value, "payload.result")?;
    let safety: SyntheticSafetyWire = parse_closed(safety_value, "payload.safety")?;
    if contract.fake_default
        || trace.fake
        || result.fake
        || safety.fake
        || is_fake_backend(&contract.backend)
        || is_fake_backend(&trace.backend)
        || is_fake_backend(&trace.provider)
        || is_fake_backend(&result.backend)
        || is_fake_backend(&result.provider)
    {
        return Err((
            "fake_backend_denied",
            "fake synthetic bundle members are not admissible".to_owned(),
            Some("payload.bundle"),
        ));
    }
    if contract.candidate_id != request.candidate_id {
        return Err((
            "bundle_candidate_mismatch",
            "synthetic contract candidate does not match the request".to_owned(),
            Some("payload.contract.candidate_id"),
        ));
    }
    if contract.claim_id != request.claim_id {
        return Err((
            "bundle_claim_mismatch",
            "synthetic contract claim does not match the request".to_owned(),
            Some("payload.contract.claim_id"),
        ));
    }
    if contract.backend != "local_synthetic" {
        return Err((
            "bundle_backend_denied",
            "synthetic contract backend is not authorized".to_owned(),
            Some("payload.contract.backend"),
        ));
    }
    require_metadata(
        contract_ref,
        &exact_metadata("stage_c", "local_synthetic", "local", None),
    )?;
    require_metadata(
        input_ref,
        &exact_metadata("stage_c", "local_synthetic", "local", None),
    )?;
    require_metadata(
        trace_ref,
        &exact_metadata(
            "stage_c",
            "local_synthetic",
            "local",
            Some("synthetic_experiment"),
        ),
    )?;
    require_metadata(
        output_ref,
        &exact_metadata(
            "stage_c",
            "local_synthetic",
            "local",
            Some("synthetic_experiment"),
        ),
    )?;
    require_metadata(
        result_ref,
        &exact_metadata(
            "stage_c",
            "local_synthetic",
            "local",
            Some("synthetic_experiment"),
        ),
    )?;
    require_metadata(
        safety_ref,
        &exact_metadata("stage_c", "local_synthetic", "local", None),
    )?;
    if contract.candidate_id != request.candidate_id
        || contract.claim_id != request.claim_id
        || contract.experiment_id.trim().is_empty()
        || contract.backend != "local_synthetic"
        || contract.data_regime != "SyntheticOnly"
        || !SUPPORTED_SYNTHETIC_EXPERIMENT_KINDS.contains(&contract.experiment_kind.as_str())
        || contract.synthetic_data_spec.is_empty()
        || contract.metrics.is_empty()
        || contract.acceptance_criteria.is_empty()
        || !(1..=100).contains(&contract.replications)
        || !(1..=60).contains(&contract.timeout_seconds)
        || contract
            .runner_name
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
        || !contract.allow_external_tools
        || contract.allow_external_calls
        || contract.fake_default
        || contract.is_verification_evidence
        || contract.expected_output_type != "synthetic_experiment_result"
        || !contains_required_forbidden_inputs(&contract.forbidden_external_inputs)
        || contains_network_or_absolute_marker(&Value::Object(contract.synthetic_data_spec.clone()))
        || contains_network_or_absolute_marker(&Value::Object(contract.model_spec.clone()))
        || contains_network_or_absolute_marker(&Value::Object(contract.algorithm_spec.clone()))
        || contains_network_or_absolute_marker(&Value::Array(
            contract
                .forbidden_external_inputs
                .iter()
                .cloned()
                .map(Value::String)
                .collect(),
        ))
    {
        return Err((
            "bundle_contract_invalid",
            "synthetic contract violates strict bundle requirements".to_owned(),
            Some("payload.contract"),
        ));
    }
    let expected_input = synthetic_input_projection(&contract);
    if serde_json::to_value(&input)
        .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload.input")))?
        != expected_input
        || sha256_canonical(&expected_input)? != result.input_spec_hash
    {
        return Err((
            "bundle_payload_hash_mismatch",
            "synthetic input does not match its contract or result hash".to_owned(),
            Some("payload.input"),
        ));
    }
    if output.metrics != result.metrics
        || output.synthetic_only == Some(false)
        || contract
            .acceptance_criteria
            .keys()
            .any(|metric| !contract.metrics.iter().any(|declared| declared == metric))
        || result
            .metrics
            .keys()
            .any(|metric| !contract.metrics.iter().any(|declared| declared == metric))
        || output.metrics.values().any(|value| {
            value.as_f64().is_none() || !value.as_f64().unwrap_or_default().is_finite()
        })
        || result.metrics.is_empty()
        || !acceptance_satisfied(&result.metrics, &contract.acceptance_criteria)
        || result.acceptance_criteria != contract.acceptance_criteria
    {
        return Err((
            "bundle_metrics_invalid",
            "synthetic metrics or acceptance criteria are invalid".to_owned(),
            Some("payload.result.metrics"),
        ));
    }
    if result.candidate_id != request.candidate_id
        || result.claim_id != request.claim_id
        || result.experiment_id != contract.experiment_id
        || result.backend != "local_synthetic"
        || result.provider != "local"
        || result.experiment_kind != contract.experiment_kind
        || result.data_regime != "SyntheticOnly"
        || result.runner_name != contract.runner_name.clone().unwrap_or_default()
        || result.exit_code != 0
        || !result.passed
        || result.reason.trim().is_empty()
        || result.label != "SyntheticExperimentVerified"
        || result.fake
        || result.raw_trace_artifact_id.as_deref() != Some(trace_id)
        || result.safety_report_artifact_id.as_deref() != Some(safety_id)
        || !is_sha256_hex(&result.stdout_hash)
        || !is_sha256_hex(&result.stderr_hash)
        || !is_sha256_hex(&result.input_spec_hash)
        || !is_sha256_hex(&result.output_payload_hash)
    {
        return Err((
            "bundle_result_invalid",
            "synthetic result does not satisfy strict verification requirements".to_owned(),
            Some("payload.result"),
        ));
    }
    if sha256_canonical(output_value)? != result.output_payload_hash {
        return Err((
            "bundle_output_hash_mismatch",
            "synthetic output hash does not match the persisted result".to_owned(),
            Some("payload.output"),
        ));
    }
    if trace.backend != result.backend
        || trace.provider != result.provider
        || trace.runner_name != result.runner_name
        || trace.runner_version != result.runner_version
        || trace.exit_code != result.exit_code
        || result.elapsed_ms != Some(trace.elapsed_ms)
        || trace.fake
        || trace.is_verification_evidence
        || sha256_hex(trace.stdout.as_bytes()) != result.stdout_hash
        || sha256_hex(trace.stderr.as_bytes()) != result.stderr_hash
    {
        return Err((
            "bundle_trace_hash_mismatch",
            "synthetic trace does not match the persisted result hashes".to_owned(),
            Some("payload.trace"),
        ));
    }
    if safety.candidate_id != request.candidate_id
        || safety.claim_id != request.claim_id
        || !safety.contract_valid
        || !safety.result_valid
        || !safety.contract_reasons.is_empty()
        || !safety.result_reasons.is_empty()
        || safety.is_verification_evidence
        || safety.fake
    {
        return Err((
            "bundle_safety_invalid",
            "synthetic safety report is not a clean validation record".to_owned(),
            Some("payload.safety"),
        ));
    }
    require_commit_payload(
        commit,
        result_value.as_object().expect("closed object schema"),
        &[
            ("experiment_backend", "backend"),
            ("experiment_provider", "provider"),
            ("experiment_contract_id", "contract_artifact_id"),
            ("experiment_result_id", "result_artifact_id"),
        ],
        contract_id,
        result_id,
    )?;
    Ok(())
}

fn resolve_claim_payload(
    payload: &Map<String, Value>,
    project_root: Option<&Path>,
) -> Result<(Map<String, Value>, Vec<KernelDiagnostic>), KernelOperationError> {
    let request: ClaimResolvePayload = serde_json::from_value(Value::Object(payload.clone()))
        .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;
    if !is_safe_segment(&request.run_id)
        || !is_safe_segment(&request.claim_id)
        || !is_safe_segment(&request.claim_table.artifact_id)
        || !is_sha256_hex(&request.claim_table.producing_commit_hash)
    {
        return Err((
            "protocol_invalid",
            "claim locators contain invalid identifiers or hashes".to_owned(),
            Some("payload"),
        ));
    }
    let root = project_root.ok_or((
        "kernel_root_missing",
        "claim resolution requires --root <project-root>".to_owned(),
        None,
    ))?;
    let claim = load_persisted_claim(&request, root)?;

    let mut evidence_bundle_validated = false;
    let mut bundle_kind = None;
    let mut evidence_ids_match = false;
    if let Some(evidence) = &request.evidence {
        let bundle_payload = map_value([
            ("run_id", Value::String(request.run_id.clone())),
            ("candidate_id", Value::String(claim.candidate_id.clone())),
            ("claim_id", Value::String(request.claim_id.clone())),
            (
                "producing_commit_hash",
                Value::String(evidence.producing_commit_hash.clone()),
            ),
            (
                "bundle",
                serde_json::to_value(&evidence.bundle).map_err(|error| {
                    (
                        "protocol_invalid",
                        error.to_string(),
                        Some("payload.evidence.bundle"),
                    )
                })?,
            ),
        ]);
        let bundle_result = validate_evidence_bundle_payload(&bundle_payload, project_root)?;
        bundle_kind = bundle_result
            .get("bundle_kind")
            .and_then(Value::as_str)
            .map(str::to_owned);
        evidence_bundle_validated = true;
        let expected_ids = authority_member_ids(&evidence.bundle);
        let claim_ids = claim
            .evidence_artifact_ids
            .iter()
            .map(String::as_str)
            .collect::<HashSet<_>>();
        evidence_ids_match = expected_ids == claim_ids;
    }

    let lowered_claim = claim.claim_text.to_lowercase();
    let synthetic_text = synthetic_claim_text_is_bounded(&lowered_claim);
    let main_text_sections = [
        "Abstract",
        "Introduction",
        "Model",
        "Theory",
        "Synthetic Experiments",
        "Results",
        "Negative Results",
        "Limitations",
    ];
    let blocked_by_main_text =
        !claim.allowed_in_main_text && main_text_sections.contains(&claim.allowed_section.as_str());
    let label_admissible = match claim.claim_label.as_str() {
        "LeanVerified" => {
            evidence_bundle_validated
                && evidence_ids_match
                && bundle_kind.as_deref() == Some("LeanProof")
        }
        "SyntheticExperimentVerified" => {
            evidence_bundle_validated
                && evidence_ids_match
                && bundle_kind.as_deref() == Some("SyntheticExperiment")
                && ["Abstract", "Synthetic Experiments", "Results"]
                    .contains(&claim.allowed_section.as_str())
                && synthetic_text
        }
        "ExperimentVerified" | "RealDataExperimentVerified" => false,
        "Conjecture" => {
            ["Theory", "Future Work", "Appendix"].contains(&claim.allowed_section.as_str())
        }
        "NegativeResult" => {
            ["Negative Results", "Results", "Limitations"].contains(&claim.allowed_section.as_str())
        }
        "Limitation" => claim.allowed_section == "Limitations",
        "Unsupported" => claim.allowed_section == "Future Work" && !claim.allowed_in_main_text,
        _ => {
            return Err((
                "claim_record_invalid",
                "claim label is not supported by the current kernel contract".to_owned(),
                Some("payload.claim_table"),
            ));
        }
    };
    let supplied_evidence_matches = !evidence_bundle_validated || evidence_ids_match;
    let admissible = label_admissible && !blocked_by_main_text && supplied_evidence_matches;
    let mut diagnostics = Vec::new();
    if matches!(
        claim.claim_label.as_str(),
        "LeanVerified" | "SyntheticExperimentVerified"
    ) && !evidence_bundle_validated
    {
        diagnostics.push(KernelDiagnostic {
            code: "claim_evidence_missing".to_owned(),
            message: "verified claims require a revalidated persisted evidence bundle".to_owned(),
            path: Some("payload.evidence".to_owned()),
        });
    }
    if evidence_bundle_validated && !evidence_ids_match {
        diagnostics.push(KernelDiagnostic {
            code: "claim_evidence_mismatch".to_owned(),
            message: "claim-table evidence IDs do not match the strict evidence bundle".to_owned(),
            path: Some("payload.claim_table".to_owned()),
        });
    }
    if claim.claim_label == "SyntheticExperimentVerified" && !synthetic_text {
        diagnostics.push(KernelDiagnostic {
            code: "claim_scope_denied".to_owned(),
            message: "persisted synthetic claim text exceeds the bounded synthetic scope"
                .to_owned(),
            path: Some("payload.claim_table".to_owned()),
        });
    }
    if !admissible && diagnostics.is_empty() {
        diagnostics.push(KernelDiagnostic {
            code: "claim_not_admissible".to_owned(),
            message:
                "claim label, section, text, and evidence do not satisfy the bounded claim contract"
                    .to_owned(),
            path: Some("payload".to_owned()),
        });
    }
    Ok((
        map_value([
            ("run_id", Value::String(request.run_id)),
            ("candidate_id", Value::String(claim.candidate_id)),
            ("claim_id", Value::String(request.claim_id)),
            (
                "claim_text_hash",
                Value::String(sha256_hex(claim.claim_text.as_bytes())),
            ),
            ("claim_label", Value::String(claim.claim_label)),
            (
                "allowed_in_main_text",
                Value::Bool(claim.allowed_in_main_text),
            ),
            ("allowed_section", Value::String(claim.allowed_section)),
            ("claim_record_validated", Value::Bool(true)),
            ("admissible", Value::Bool(admissible)),
            (
                "evidence_bundle_validated",
                Value::Bool(evidence_bundle_validated),
            ),
            ("authority_granted", Value::Bool(false)),
        ]),
        diagnostics,
    ))
}

fn load_persisted_claim(
    request: &ClaimResolvePayload,
    root: &Path,
) -> Result<ClaimWire, KernelOperationError> {
    let ledger = load_persisted_ledger(root, &request.run_id)?;
    verify_ledger(&ledger)?;
    let commit = ledger
        .commits
        .iter()
        .find(|commit| commit.commit_hash == request.claim_table.producing_commit_hash)
        .ok_or((
            "claim_record_missing",
            "claim-table producing commit is absent from the persisted run ledger".to_owned(),
            Some("payload.claim_table.producing_commit_hash"),
        ))?;
    if commit.run_id != request.run_id
        || commit.action_type != "ClaimTableBuilt"
        || commit.artifact_refs.len() != 1
    {
        return Err((
            "claim_record_invalid",
            "claim-table commit has the wrong run, action, or artifact set".to_owned(),
            Some("payload.claim_table"),
        ));
    }
    let artifact: WireArtifactRef = serde_json::from_value(commit.artifact_refs[0].clone())
        .map_err(|error| {
            (
                "claim_record_invalid",
                format!("claim-table artifact reference is invalid: {error}"),
                Some("payload.claim_table"),
            )
        })?;
    if artifact.id != request.claim_table.artifact_id
        || artifact.artifact_type != "report"
        || artifact.producing_commit_hash.as_deref() != Some(commit.commit_hash.as_str())
        || artifact.metadata
            != map_value([
                ("format", Value::String("json".to_owned())),
                ("stage", Value::String("manuscript_planning".to_owned())),
                ("fake", Value::Bool(true)),
            ])
    {
        return Err((
            "claim_record_invalid",
            "claim-table artifact identity or metadata is invalid".to_owned(),
            Some("payload.claim_table"),
        ));
    }
    validate_artifact_location(&request.run_id, &artifact).map_err(|message| {
        (
            "artifact_path_invalid",
            message,
            Some("payload.claim_table"),
        )
    })?;
    let path = resolve_run_file(root, &request.run_id, &artifact.path).map_err(|message| {
        (
            "artifact_path_invalid",
            message,
            Some("payload.claim_table"),
        )
    })?;
    let bytes = fs::read(&path).map_err(|error| {
        (
            "artifact_missing",
            format!("claim-table artifact cannot be read: {error}"),
            Some("payload.claim_table"),
        )
    })?;
    if sha256_hex(&bytes) != artifact.content_hash {
        return Err((
            "artifact_hash_mismatch",
            "claim-table artifact hash does not match its persisted bytes".to_owned(),
            Some("payload.claim_table"),
        ));
    }
    let value = parse_json_without_duplicate_keys(std::str::from_utf8(&bytes).map_err(|_| {
        (
            "claim_record_invalid",
            "claim-table artifact must contain UTF-8 JSON".to_owned(),
            Some("payload.claim_table"),
        )
    })?)
    .map_err(|error| {
        (
            "claim_record_invalid",
            format!("claim-table JSON is invalid: {error}"),
            Some("payload.claim_table"),
        )
    })?;
    if value != Value::Object(commit.payload.clone()) {
        return Err((
            "claim_record_invalid",
            "claim-table artifact does not equal its producing commit payload".to_owned(),
            Some("payload.claim_table"),
        ));
    }
    let table: ClaimTableWire =
        parse_closed(&value, "payload.claim_table").map_err(|(_, message, _)| {
            (
                "claim_record_invalid",
                format!("claim-table artifact violates the closed claim contract: {message}"),
                Some("payload.claim_table"),
            )
        })?;
    if table.final_nucleus_id.trim().is_empty() {
        return Err((
            "claim_record_invalid",
            "claim table has an empty final nucleus ID".to_owned(),
            Some("payload.claim_table"),
        ));
    }
    let mut claim_ids = HashSet::new();
    for claim in &table.claims {
        if !claim_ids.insert(claim.claim_id.as_str()) {
            return Err((
                "claim_record_invalid",
                "claim table contains duplicate claim IDs".to_owned(),
                Some("payload.claim_table"),
            ));
        }
    }
    for link in &table.evidence_links {
        if !is_safe_segment(&link.claim_id)
            || !is_safe_segment(&link.artifact_id)
            || link.artifact_type.trim().is_empty()
            || link.evidence_role.as_deref().is_some_and(str::is_empty)
        {
            return Err((
                "claim_record_invalid",
                "claim table contains an invalid evidence link".to_owned(),
                Some("payload.claim_table"),
            ));
        }
        let _ = link.supports_label;
    }
    let claim = table
        .claims
        .into_iter()
        .find(|claim| claim.claim_id == request.claim_id)
        .ok_or((
            "claim_record_missing",
            "requested claim is absent from the persisted claim table".to_owned(),
            Some("payload.claim_id"),
        ))?;
    validate_claim_record(&claim)?;
    Ok(claim)
}

fn validate_claim_record(claim: &ClaimWire) -> Result<(), KernelOperationError> {
    let allowed_sections = [
        "Abstract",
        "Introduction",
        "Related Work",
        "Model",
        "Theory",
        "Synthetic Experiments",
        "Results",
        "Negative Results",
        "Limitations",
        "Future Work",
        "Appendix",
    ];
    if !is_safe_segment(&claim.claim_id)
        || !is_safe_segment(&claim.candidate_id)
        || claim.claim_text.trim().is_empty()
        || claim.reason.trim().is_empty()
        || !allowed_sections.contains(&claim.allowed_section.as_str())
        || !matches!(
            claim.claim_label.as_str(),
            "LeanVerified"
                | "ExperimentVerified"
                | "SyntheticExperimentVerified"
                | "RealDataExperimentVerified"
                | "Conjecture"
                | "NegativeResult"
                | "Limitation"
                | "Unsupported"
        )
        || claim
            .evidence_artifact_ids
            .iter()
            .any(|artifact_id| !is_safe_segment(artifact_id))
        || claim
            .evidence_artifact_ids
            .iter()
            .collect::<HashSet<_>>()
            .len()
            != claim.evidence_artifact_ids.len()
        || claim
            .evidence_types
            .iter()
            .any(|kind| kind.trim().is_empty())
        || claim.evidence_types.iter().collect::<HashSet<_>>().len() != claim.evidence_types.len()
    {
        return Err((
            "claim_record_invalid",
            "persisted claim record violates the closed claim contract".to_owned(),
            Some("payload.claim_table"),
        ));
    }
    Ok(())
}

fn authority_member_ids(bundle: &EvidenceBundle) -> HashSet<&str> {
    match bundle {
        EvidenceBundle::Lean {
            trace_artifact_id,
            result_artifact_id,
            ..
        } => [trace_artifact_id.as_str(), result_artifact_id.as_str()]
            .into_iter()
            .collect(),
        EvidenceBundle::Synthetic {
            trace_artifact_id,
            output_artifact_id,
            result_artifact_id,
            ..
        } => [
            trace_artifact_id.as_str(),
            output_artifact_id.as_str(),
            result_artifact_id.as_str(),
        ]
        .into_iter()
        .collect(),
    }
}

fn synthetic_claim_text_is_bounded(lowered: &str) -> bool {
    let has_scope_token = lowered
        .split(|character: char| !character.is_alphanumeric())
        .any(|token| matches!(token, "synthetic" | "simulation"));
    let forbidden = [
        "real-world",
        "real world",
        "empirical validation",
        "external validity",
        "deploy",
        "deployment",
        "generalize",
        "generalization",
        "universal",
    ];
    has_scope_token && !forbidden.iter().any(|phrase| lowered.contains(phrase))
}

fn require_commit_payload(
    commit: &WireLedgerCommit,
    result: &Map<String, Value>,
    aliases: &[(&str, &str)],
    contract_id: &str,
    result_id: &str,
) -> Result<(), (&'static str, String, Option<&'static str>)> {
    if aliases.len() != 4 {
        return Err((
            "internal_error",
            "kernel commit-payload alias configuration is invalid".to_owned(),
            None,
        ));
    }
    let expected_keys = result.len() + aliases.len();
    if commit.payload.len() != expected_keys {
        return Err((
            "bundle_commit_mismatch",
            "producing commit payload has unexpected fields".to_owned(),
            Some("payload.producing_commit_hash"),
        ));
    }
    for (key, value) in result {
        if commit.payload.get(key) != Some(value) {
            return Err((
                "bundle_commit_mismatch",
                format!("producing commit result field does not match: {key}"),
                Some("payload.producing_commit_hash"),
            ));
        }
    }
    let expected_aliases = [
        (
            aliases[0].0,
            result.get(aliases[0].1).cloned().unwrap_or(Value::Null),
        ),
        (
            aliases[1].0,
            result.get(aliases[1].1).cloned().unwrap_or(Value::Null),
        ),
        (aliases[2].0, Value::String(contract_id.to_owned())),
        (aliases[3].0, Value::String(result_id.to_owned())),
    ];
    for (key, value) in expected_aliases {
        if commit.payload.get(key) != Some(&value) {
            return Err((
                "bundle_commit_mismatch",
                format!("producing commit link does not match: {key}"),
                Some("payload.producing_commit_hash"),
            ));
        }
    }
    Ok(())
}

fn imports_are_allowed(payload: &str, allowed_imports: &[String]) -> bool {
    let allowed: HashSet<&str> = allowed_imports.iter().map(String::as_str).collect();
    payload.lines().all(|line| {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with("--") {
            return true;
        }
        let mut tokens = trimmed.split_whitespace();
        if tokens.next() != Some("import") {
            return true;
        }
        let modules: Vec<&str> = tokens.collect();
        !modules.is_empty()
            && modules.iter().all(|module| {
                module
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'.')
                    && allowed.contains(module)
            })
    })
}

fn allowed_imports_are_safe(allowed_imports: &[String]) -> bool {
    allowed_imports.iter().all(|module| {
        !module.is_empty()
            && !contains_network_marker(module)
            && module.split('.').all(|segment| {
                !segment.is_empty()
                    && segment
                        .bytes()
                        .next()
                        .is_some_and(|byte| byte.is_ascii_alphabetic() || byte == b'_')
                    && segment
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
            })
    })
}

fn contains_forbidden_proof_token(
    claim_text: &str,
    payload_text: &str,
    forbidden_tokens: &[String],
) -> bool {
    let lower = format!("{claim_text}\n{payload_text}").to_lowercase();
    if forbidden_tokens.is_empty() {
        DEFAULT_FORBIDDEN_PROOF_TOKENS
            .iter()
            .any(|token| lower.contains(token))
    } else {
        forbidden_tokens
            .iter()
            .any(|token| lower.contains(&token.to_lowercase()))
    }
}

fn verify_checkpoint_payload(
    payload: &Map<String, Value>,
    project_root: Option<&Path>,
) -> Result<(Map<String, Value>, Vec<KernelDiagnostic>), KernelOperationError> {
    if payload.len() != 2 || !payload.contains_key("run_id") || !payload.contains_key("index") {
        return Err((
            "protocol_invalid",
            "checkpoint.verify payload must contain exactly run_id and index fields".to_owned(),
            Some("payload"),
        ));
    }
    let request: CheckpointVerifyPayload =
        serde_json::from_value(Value::Object(payload.clone()))
            .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;
    let root = project_root.ok_or((
        "kernel_root_missing",
        "checkpoint verification requires --root <project-root>".to_owned(),
        None,
    ))?;
    let ledger = load_persisted_ledger(root, &request.run_id)?;
    verify_ledger(&ledger)?;
    let requested_commit = ledger
        .commits
        .iter()
        .find(|commit| commit.commit_hash == request.index.producing_commit_hash)
        .ok_or((
            "checkpoint_index_missing",
            "checkpoint index producer commit is absent from the persisted ledger".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ))?;
    if requested_commit.action_type != "AutonomousPaperCheckpointWritten" {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint index producer commit has the wrong action type".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    }
    let latest_commit = ledger
        .commits
        .iter()
        .rev()
        .find(|commit| commit.action_type == "AutonomousPaperCheckpointWritten")
        .ok_or((
            "checkpoint_index_missing",
            "the persisted ledger contains no autonomous checkpoint commit".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ))?;
    if latest_commit.commit_hash != requested_commit.commit_hash {
        return Err((
            "checkpoint_not_latest",
            "checkpoint index locator does not identify the latest autonomous checkpoint commit"
                .to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    }
    let (index_ref, _) = checkpoint_commit_refs(requested_commit, &request.run_id, None, None)?;
    if index_ref.id != request.index.artifact_id {
        return Err((
            "checkpoint_index_invalid",
            "requested checkpoint index artifact does not match its producer commit".to_owned(),
            Some("payload.index.artifact_id"),
        ));
    }
    validate_checkpoint_artifact_ref(&index_ref, &request.run_id, requested_commit)?;
    let index_path =
        resolve_run_file(root, &request.run_id, &index_ref.path).map_err(|message| {
            (
                "checkpoint_index_missing",
                message,
                Some("payload.index.artifact_id"),
            )
        })?;
    if sha256_file(&index_path).map_err(|message| {
        (
            "checkpoint_index_missing",
            message,
            Some("payload.index.artifact_id"),
        )
    })? != index_ref.content_hash
    {
        return Err((
            "artifact_hash_mismatch",
            "checkpoint index bytes do not match the ledger artifact hash".to_owned(),
            Some("payload.index.artifact_id"),
        ));
    }
    let index_value = read_json_without_duplicate_keys(&index_path).map_err(|message| {
        (
            "checkpoint_index_invalid",
            message,
            Some("payload.index.artifact_id"),
        )
    })?;
    let index: CheckpointIndexWire =
        parse_closed(&index_value, "payload.index").map_err(|(_, message, _)| {
            (
                "checkpoint_index_invalid",
                message,
                Some("payload.index.artifact_id"),
            )
        })?;
    validate_checkpoint_index(&index, &request.run_id, &request.index.artifact_id)?;

    let mut checkpoint_hashes = Vec::with_capacity(index.checkpoint_count);
    let mut validated_output_count = 0_usize;
    let mut previous_hash: Option<String> = None;
    let mut latest_resume_allowed = false;
    let mut latest_stage = String::new();
    let mut latest_controller = String::new();
    let mut terminal_failure_seen = false;
    for (position, relative) in index.checkpoints.iter().enumerate() {
        let number = position + 1;
        let checkpoint_commit = ledger
            .commits
            .iter()
            .take_while(|commit| commit.commit_hash != requested_commit.commit_hash)
            .chain(std::iter::once(requested_commit))
            .find(|commit| {
                commit.action_type == "AutonomousPaperCheckpointWritten"
                    && commit.artifact_refs.iter().any(|raw| {
                        raw.get("path").and_then(Value::as_str) == Some(relative.as_str())
                    })
            })
            .ok_or((
                "checkpoint_record_missing",
                "checkpoint path is not linked by an earlier checkpoint commit".to_owned(),
                Some("payload.index.checkpoints"),
            ))?;
        let (historical_index_ref, checkpoint_ref) = checkpoint_commit_refs(
            checkpoint_commit,
            &request.run_id,
            Some(number),
            Some(relative),
        )?;
        validate_checkpoint_artifact_ref(
            &historical_index_ref,
            &request.run_id,
            checkpoint_commit,
        )?;
        validate_checkpoint_artifact_ref(&checkpoint_ref, &request.run_id, checkpoint_commit)?;
        let checkpoint_path =
            resolve_run_file(root, &request.run_id, relative).map_err(|message| {
                (
                    "checkpoint_record_missing",
                    message,
                    Some("payload.index.checkpoints"),
                )
            })?;
        if sha256_file(&checkpoint_path).map_err(|message| {
            (
                "checkpoint_record_missing",
                message,
                Some("payload.index.checkpoints"),
            )
        })? != checkpoint_ref.content_hash
        {
            return Err((
                "artifact_hash_mismatch",
                "checkpoint bytes do not match the ledger artifact hash".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
        let checkpoint_value =
            read_json_without_duplicate_keys(&checkpoint_path).map_err(|message| {
                (
                    "checkpoint_record_invalid",
                    message,
                    Some("payload.index.checkpoints"),
                )
            })?;
        let checkpoint: CheckpointWire = parse_closed(&checkpoint_value, "payload.checkpoint")
            .map_err(|(_, message, _)| {
                (
                    "checkpoint_record_invalid",
                    message,
                    Some("payload.index.checkpoints"),
                )
            })?;
        validate_checkpoint_record(
            root,
            &request.run_id,
            checkpoint_commit,
            &checkpoint,
            previous_hash.as_deref(),
            number,
        )?;
        let expected_id = format!(
            "autonomous-paper-checkpoint-{number:04}-{}",
            checkpoint.stage_name.replace('_', "-")
        );
        if checkpoint_ref.id != expected_id {
            return Err((
                "checkpoint_record_invalid",
                "checkpoint artifact id does not match its stage".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
        if relative != &format!("runs/{}/reports/{}.json", request.run_id, checkpoint_ref.id) {
            return Err((
                "checkpoint_record_invalid",
                "checkpoint path is not canonical for its artifact id".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
        validate_checkpoint_commit_payload(checkpoint_commit, &checkpoint)?;
        validate_checkpoint_index_snapshot(
            root,
            &request.run_id,
            &historical_index_ref,
            &index.checkpoints[..number],
            number,
            &checkpoint,
        )?;
        validated_output_count += checkpoint.output_hashes.len();
        let checkpoint_reusable = checkpoint_is_reusable(&checkpoint).map_err(|message| {
            (
                "checkpoint_record_invalid",
                message,
                Some("payload.index.checkpoints"),
            )
        })?;
        if terminal_failure_seen {
            return Err((
                "checkpoint_chain_mismatch",
                "a checkpoint follows a terminal failed or blocked checkpoint".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
        if !checkpoint_reusable {
            terminal_failure_seen = true;
        }
        latest_resume_allowed = checkpoint_reusable;
        latest_stage = checkpoint.stage_name.clone();
        latest_controller = checkpoint.controller_run_id.clone();
        previous_hash = Some(checkpoint.checkpoint_hash.clone());
        checkpoint_hashes.push(checkpoint.checkpoint_hash);
    }
    if index.publication_ready
        || index.creates_scientific_validation
        || index.implies_publication_readiness
        || index.is_verification_evidence
    {
        return Err((
            "checkpoint_authority_violation",
            "checkpoint index claims forbidden authority".to_owned(),
            Some("payload.index"),
        ));
    }
    if index.latest_controller_run_id != latest_controller
        || index.latest_completed_stage.as_deref() != Some(latest_stage.as_str())
        || index.resume_allowed != latest_resume_allowed
    {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint index fields do not match the validated checkpoint chain".to_owned(),
            Some("payload.index"),
        ));
    }
    let diagnostics = if latest_resume_allowed {
        Vec::new()
    } else {
        vec![KernelDiagnostic {
            code: "checkpoint_not_reusable".to_owned(),
            message: "the latest checkpoint is structurally valid but is not reusable".to_owned(),
            path: Some("payload.index.resume_allowed".to_owned()),
        }]
    };
    Ok((
        map_value([
            ("run_id", Value::String(request.run_id)),
            (
                "checkpoint_index_artifact_id",
                Value::String(request.index.artifact_id),
            ),
            (
                "checkpoint_index_producing_commit_hash",
                Value::String(request.index.producing_commit_hash),
            ),
            (
                "checkpoint_count",
                Value::Number((index.checkpoint_count as u64).into()),
            ),
            (
                "validated_checkpoint_hashes",
                Value::Array(checkpoint_hashes.into_iter().map(Value::String).collect()),
            ),
            (
                "latest_checkpoint_hash",
                Value::String(previous_hash.expect("checkpoint count is positive")),
            ),
            ("latest_completed_stage", Value::String(latest_stage)),
            (
                "validated_output_count",
                Value::Number((validated_output_count as u64).into()),
            ),
            ("checkpoint_chain_valid", Value::Bool(true)),
            ("resume_allowed", Value::Bool(latest_resume_allowed)),
            ("authority_granted", Value::Bool(false)),
        ]),
        diagnostics,
    ))
}

const REPLAY_REQUIRED_OUTPUTS: [(&str, &str); 11] = [
    ("FinalNucleusSelected", "id"),
    ("ClaimTableBuilt", "claims"),
    ("ManuscriptPlanBuilt", "sections"),
    ("DraftSkeletonBuilt", "section_stubs"),
    ("ArtifactManifestWritten", "artifacts"),
    ("BranchOutcomesWritten", "branch_outcomes"),
    ("ResearchObjectWritten", "final_nucleus"),
    ("PaperSkeletonWritten", "paper_id"),
    ("FinalAuditReportWritten", "checks"),
    ("ReleaseGateDecided", "status"),
    ("ExportReadinessReportWritten", "ready_for_polished_prose"),
];

fn replay_error(code: &'static str, message: impl Into<String>) -> KernelOperationError {
    (code, message.into(), Some("payload"))
}

fn scan_forbidden_authority_values(value: &Value) -> bool {
    match value {
        Value::String(text) => matches!(
            text.as_str(),
            "ExperimentVerified" | "RealDataExperimentVerified"
        ),
        Value::Array(items) => items.iter().any(scan_forbidden_authority_values),
        Value::Object(object) => {
            let forbidden_true = [
                "accepted_for_publication",
                "accepted_paper",
                "certifies_scientific_validity",
                "creates_scientific_validation",
                "human_approval_granted",
                "human_approved",
                "implies_publication_readiness",
                "novelty_proven",
                "publication_ready",
            ];
            object.iter().any(|(key, item)| {
                (forbidden_true.contains(&key.as_str()) && item == &Value::Bool(true))
                    || scan_forbidden_authority_values(item)
            })
        }
        _ => false,
    }
}

fn replay_artifact_json(
    root: &Path,
    run_id: &str,
    artifact: &WireArtifactRef,
    commit: &WireLedgerCommit,
) -> Result<(PathBuf, Value), KernelOperationError> {
    let path = resolve_run_file(root, run_id, &artifact.path)
        .map_err(|message| replay_error("replay_required_output_invalid", message))?;
    let bytes = fs::read(&path)
        .map_err(|error| replay_error("replay_required_output_invalid", error.to_string()))?;
    if sha256_hex(&bytes) != artifact.content_hash {
        return Err(replay_error(
            "replay_required_output_invalid",
            format!(
                "artifact {} hash does not match persisted bytes",
                artifact.id
            ),
        ));
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| {
        replay_error(
            "replay_required_output_invalid",
            "JSON artifact is not UTF-8",
        )
    })?;
    let value = parse_json_without_duplicate_keys(text)
        .map_err(|error| replay_error("replay_required_output_invalid", error.to_string()))?;
    if value != Value::Object(commit.payload.clone()) {
        return Err(replay_error(
            "replay_required_output_invalid",
            format!(
                "artifact {} does not equal its producing commit payload",
                artifact.id
            ),
        ));
    }
    Ok((path, value))
}

fn resolve_replay_output<'a>(
    root: &Path,
    run_id: &str,
    commits: &'a [WireLedgerCommit],
    action: &str,
    key: &str,
) -> Result<(&'a WireLedgerCommit, WireArtifactRef, Value), KernelOperationError> {
    let commit = commits
        .iter()
        .rev()
        .find(|candidate| candidate.action_type == action && candidate.payload.contains_key(key))
        .ok_or_else(|| {
            replay_error(
                "replay_required_output_missing",
                format!("missing {action} output"),
            )
        })?;
    let refs = commit
        .artifact_refs
        .iter()
        .filter_map(|raw| serde_json::from_value::<WireArtifactRef>(raw.clone()).ok())
        .filter(|artifact| artifact.path.ends_with(".json"))
        .collect::<Vec<_>>();
    if refs.len() != 1 {
        return Err(replay_error(
            "replay_required_output_invalid",
            format!("{action} must contain exactly one JSON artifact"),
        ));
    }
    let artifact = refs.into_iter().next().expect("checked length");
    let (_, value) = replay_artifact_json(root, run_id, &artifact, commit)?;
    Ok((commit, artifact, value))
}

fn verify_replay_core_payload(
    payload: &Map<String, Value>,
    project_root: Option<&Path>,
) -> Result<Map<String, Value>, KernelOperationError> {
    validate_replay_core_payload_structure(payload)
        .map_err(|message| replay_error("protocol_invalid", message))?;
    let request: ReplayVerifyCorePayload =
        serde_json::from_value(Value::Object(payload.clone()))
            .map_err(|error| replay_error("protocol_invalid", error.to_string()))?;
    let root = project_root.ok_or_else(|| {
        replay_error(
            "kernel_root_missing",
            "replay verification requires --root <project-root>",
        )
    })?;
    let ledger = load_persisted_ledger(root, &request.run_id)
        .map_err(|(_, message, _)| replay_error("replay_required_output_invalid", message))?;
    if ledger.commits.is_empty() {
        return Err(replay_error(
            "replay_not_complete",
            "persisted run ledger is empty",
        ));
    }
    verify_ledger(&ledger)
        .map_err(|(_, message, _)| replay_error("replay_required_output_invalid", message))?;
    let tip = ledger
        .commits
        .last()
        .expect("non-empty ledger")
        .commit_hash
        .as_str();
    if tip != request.ledger_tip_hash {
        return Err(replay_error(
            "replay_not_latest",
            "requested ledger tip is not the current tip",
        ));
    }

    let mut inventory = Vec::new();
    let mut verified_artifacts = Vec::new();
    let mut paths = HashSet::new();
    let mut identities = HashSet::new();
    for commit in &ledger.commits {
        for raw in &commit.artifact_refs {
            if !raw.as_object().is_some_and(|object| {
                object.contains_key("producing_commit_hash") && object.contains_key("metadata")
            }) {
                return Err(replay_error(
                    "replay_required_output_invalid",
                    "artifact reference must include producing_commit_hash and metadata",
                ));
            }
            let artifact: WireArtifactRef =
                serde_json::from_value(raw.clone()).map_err(|error| {
                    replay_error(
                        "replay_required_output_invalid",
                        format!("invalid artifact reference: {error}"),
                    )
                })?;
            validate_artifact_ref(raw)
                .map_err(|message| replay_error("replay_required_output_invalid", message))?;
            if scan_forbidden_authority_values(raw) {
                return Err(replay_error(
                    "replay_authority_violation",
                    "artifact reference claims forbidden authority",
                ));
            }
            if artifact.producing_commit_hash.as_deref() != Some(commit.commit_hash.as_str()) {
                return Err(replay_error(
                    "replay_required_output_invalid",
                    "artifact reference must self-link to its containing commit",
                ));
            }
            validate_artifact_location(&request.run_id, &artifact)
                .map_err(|message| replay_error("replay_required_output_invalid", message))?;
            if artifact
                .path
                .split('/')
                .any(|part| matches!(part, "replay" | "diagnostics" | "comparisons"))
            {
                return Err(replay_error(
                    "replay_authority_violation",
                    "derived replay artifact path cannot be authoritative",
                ));
            }
            if !paths.insert(artifact.path.clone()) {
                return Err(replay_error(
                    "replay_required_output_invalid",
                    "duplicate artifact path in ledger",
                ));
            }
            let identity = (
                artifact.id.clone(),
                artifact.path.clone(),
                artifact.content_hash.clone(),
                artifact.artifact_type.clone(),
                artifact.metadata.clone(),
            );
            if !identities.insert(identity) {
                return Err(replay_error(
                    "replay_required_output_invalid",
                    "duplicate artifact identity in ledger",
                ));
            }
            let artifact_path = resolve_run_file(root, &request.run_id, &artifact.path)
                .map_err(|message| replay_error("replay_required_output_invalid", message))?;
            if sha256_file(&artifact_path)
                .map_err(|message| replay_error("replay_required_output_invalid", message))?
                != artifact.content_hash
            {
                return Err(replay_error(
                    "replay_required_output_invalid",
                    format!("artifact {} hash mismatch", artifact.id),
                ));
            }
            verified_artifacts.push((artifact_path.clone(), artifact.content_hash.clone()));
            if artifact.path.ends_with(".json") {
                let value = read_json_without_duplicate_keys(&artifact_path)
                    .map_err(|message| replay_error("replay_required_output_invalid", message))?;
                if scan_forbidden_authority_values(&value) {
                    return Err(replay_error(
                        "replay_authority_violation",
                        "forbidden experiment verification label in persisted JSON",
                    ));
                }
            }
            let mut item = Map::new();
            item.insert(
                "commit_hash".to_owned(),
                Value::String(commit.commit_hash.clone()),
            );
            item.insert("artifact".to_owned(), raw.clone());
            inventory.push(Value::Object(item));
        }
    }
    let inventory_json = canonical_json(&Value::Array(inventory))
        .map_err(|error| replay_error("replay_required_output_invalid", error.to_string()))?;

    if !ledger.commits.iter().any(|commit| {
        commit.action_type == "ExportReadinessReportWritten"
            && commit.payload.contains_key("ready_for_polished_prose")
    }) {
        return Err(replay_error(
            "replay_not_complete",
            "persisted run has no export-readiness completion record",
        ));
    }
    if !ledger.commits.iter().any(|commit| {
        commit.action_type == "ArtifactManifestWritten" && commit.payload.contains_key("artifacts")
    }) {
        return Err(replay_error(
            "replay_manifest_missing",
            "persisted run has no artifact manifest",
        ));
    }
    let mut resolved = Vec::new();
    for (action, key) in REPLAY_REQUIRED_OUTPUTS {
        resolved.push(resolve_replay_output(
            root,
            &request.run_id,
            &ledger.commits,
            action,
            key,
        )?);
    }
    let (manifest_commit, manifest_artifact, manifest_value) = resolved[4].clone();
    let expected_manifest_path = format!(
        "runs/{}/research_object/artifact-manifest.json",
        request.run_id
    );
    if manifest_artifact.id != "artifact-manifest"
        || manifest_artifact.artifact_type != "report"
        || manifest_artifact.path != expected_manifest_path
    {
        return Err(replay_error(
            "replay_manifest_invalid",
            "manifest artifact identity is invalid",
        ));
    }
    let manifest: ArtifactManifestWire = parse_closed(&manifest_value, "payload.manifest")
        .map_err(|(_, message, _)| replay_error("replay_manifest_invalid", message))?;
    if manifest.run_id != request.run_id || manifest.source_of_truth != "ledger" {
        return Err(replay_error(
            "replay_manifest_invalid",
            "manifest run or source_of_truth is invalid",
        ));
    }
    let mut manifest_keys = HashSet::new();
    let mut previous_key = None::<(String, String)>;
    let mut evidence_count = 0usize;
    let mut presentation_count = 0usize;
    let prefix_refs = ledger
        .commits
        .iter()
        .take_while(|c| c.commit_hash != manifest_commit.commit_hash)
        .flat_map(|c| c.artifact_refs.iter())
        .collect::<Vec<_>>();
    if prefix_refs.len() != manifest.artifacts.len() {
        return Err(replay_error(
            "replay_manifest_mismatch",
            "manifest does not match the pre-manifest ledger prefix",
        ));
    }
    for entry in &manifest.artifacts {
        let content_hash = entry.content_hash.as_deref().ok_or_else(|| {
            replay_error(
                "replay_manifest_invalid",
                "manifest entry content_hash is null",
            )
        })?;
        let producer = entry.producing_commit_hash.as_deref().ok_or_else(|| {
            replay_error("replay_manifest_invalid", "manifest entry producer is null")
        })?;
        if !is_sha256_hex(content_hash) || !is_sha256_hex(producer) {
            return Err(replay_error(
                "replay_manifest_invalid",
                "manifest entry hashes are invalid",
            ));
        }
        let key = (entry.path.clone(), entry.artifact_id.clone());
        if !manifest_keys.insert(key.clone())
            || previous_key
                .as_ref()
                .is_some_and(|previous| previous >= &key)
        {
            return Err(replay_error(
                "replay_manifest_invalid",
                "manifest entries are not unique and sorted",
            ));
        }
        previous_key = Some(key);
        let matching = prefix_refs
            .iter()
            .filter_map(|raw| serde_json::from_value::<WireArtifactRef>((*raw).clone()).ok())
            .find(|artifact| {
                artifact.id == entry.artifact_id
                    && artifact.artifact_type == entry.artifact_type
                    && artifact.path == entry.path
                    && artifact.content_hash == content_hash
                    && artifact.producing_commit_hash.as_deref() == Some(producer)
                    && artifact.metadata == entry.metadata
            });
        let Some(matching) = matching else {
            return Err(replay_error(
                "replay_manifest_mismatch",
                "manifest entry does not match a ledger artifact reference",
            ));
        };
        let derived_evidence = is_manifest_evidence(&matching);
        let derived_presentation = is_manifest_presentation(&matching, derived_evidence);
        if entry.is_presentation != derived_presentation
            || entry.is_evidence != derived_evidence
            || (entry.is_evidence && entry.is_presentation)
        {
            return Err(replay_error(
                "replay_authority_violation",
                "manifest evidence/presentation flags violate the authority boundary",
            ));
        }
        evidence_count += usize::from(entry.is_evidence);
        presentation_count += usize::from(entry.is_presentation);
    }
    if manifest.evidence_artifact_count != evidence_count
        || manifest.presentation_artifact_count != presentation_count
    {
        return Err(replay_error(
            "replay_manifest_mismatch",
            "manifest evidence/presentation counts are inconsistent",
        ));
    }
    let manifest_inventory_hash = sha256_hex(
        canonical_json(
            &manifest_value
                .get("artifacts")
                .cloned()
                .unwrap_or(Value::Null),
        )
        .map_err(|error| replay_error("replay_manifest_invalid", error.to_string()))?
        .as_bytes(),
    );

    let claim_value = &resolved[1].2;
    let claim_table: ClaimTableWire = parse_closed(claim_value, "payload.claim_table")
        .map_err(|(_, message, _)| replay_error("replay_dependency_missing", message))?;
    if claim_table.final_nucleus_id.trim().is_empty() {
        return Err(replay_error(
            "replay_dependency_missing",
            "claim table has an empty final nucleus ID",
        ));
    }
    let mut claim_ids = HashSet::new();
    for claim in &claim_table.claims {
        validate_claim_record(claim)
            .map_err(|(_, message, _)| replay_error("replay_dependency_missing", message))?;
        if !claim_ids.insert(claim.claim_id.as_str()) {
            return Err(replay_error(
                "replay_dependency_ambiguous",
                "duplicate claim ID",
            ));
        }
    }
    let mut manifest_by_id: HashMap<&str, Vec<&ArtifactManifestEntryWire>> = HashMap::new();
    for entry in &manifest.artifacts {
        manifest_by_id
            .entry(entry.artifact_id.as_str())
            .or_default()
            .push(entry);
    }
    let mut expected_supporting_pairs = HashSet::new();
    for claim in &claim_table.claims {
        let mut resolved_evidence_types = HashSet::new();
        for evidence_id in &claim.evidence_artifact_ids {
            let entries = manifest_by_id
                .get(evidence_id.as_str())
                .cloned()
                .unwrap_or_default();
            if entries.len() != 1 {
                return Err(replay_error(
                    if entries.is_empty() {
                        "replay_dependency_missing"
                    } else {
                        "replay_dependency_ambiguous"
                    },
                    "claim evidence artifact does not resolve uniquely",
                ));
            }
            let entry = entries[0];
            let matching_artifact = prefix_refs
                .iter()
                .filter_map(|raw| serde_json::from_value::<WireArtifactRef>((*raw).clone()).ok())
                .find(|artifact| artifact.id == entry.artifact_id && artifact.path == entry.path)
                .expect("manifest entry already matched a prefix artifact");
            if !entry.is_evidence
                || entry.is_presentation
                || is_forbidden_derived_evidence(&matching_artifact)
            {
                return Err(replay_error(
                    "replay_authority_violation",
                    "claim evidence artifact is not structurally admissible",
                ));
            }
            resolved_evidence_types.insert(entry.artifact_type.as_str());
            expected_supporting_pairs.insert((claim.claim_id.as_str(), evidence_id.as_str()));
        }
        let declared_evidence_types = claim
            .evidence_types
            .iter()
            .map(String::as_str)
            .collect::<HashSet<_>>();
        if resolved_evidence_types != declared_evidence_types {
            return Err(replay_error(
                "replay_dependency_mismatch",
                "claim evidence types do not match resolved manifest dependencies",
            ));
        }
    }
    let mut complete_links = HashSet::new();
    let mut pair_decisions = HashMap::new();
    let mut supporting_pairs = HashSet::new();
    for link in &claim_table.evidence_links {
        if !is_safe_segment(&link.claim_id)
            || !is_safe_segment(&link.artifact_id)
            || artifact_directory(&link.artifact_type).is_none()
            || link.evidence_role.as_deref().is_some_and(str::is_empty)
        {
            return Err(replay_error(
                "replay_dependency_mismatch",
                "claim table contains an invalid evidence link",
            ));
        }
        let complete = (
            link.claim_id.as_str(),
            link.artifact_id.as_str(),
            link.artifact_type.as_str(),
            link.evidence_role.as_deref(),
            link.supports_label,
        );
        if !complete_links.insert(complete) {
            return Err(replay_error(
                "replay_dependency_ambiguous",
                "claim table contains a duplicate evidence link",
            ));
        }
        let pair = (link.claim_id.as_str(), link.artifact_id.as_str());
        if pair_decisions
            .insert(pair, link.supports_label)
            .is_some_and(|previous| previous != link.supports_label)
        {
            return Err(replay_error(
                "replay_dependency_mismatch",
                "claim table contains contradictory evidence links",
            ));
        }
        let entries = manifest_by_id
            .get(link.artifact_id.as_str())
            .cloned()
            .unwrap_or_default();
        if !claim_ids.contains(link.claim_id.as_str()) || entries.len() != 1 {
            return Err(replay_error(
                "replay_dependency_mismatch",
                "evidence link is dangling or ambiguous",
            ));
        }
        if entries[0].artifact_type != link.artifact_type {
            return Err(replay_error(
                "replay_dependency_mismatch",
                "evidence link artifact type does not match its manifest dependency",
            ));
        }
        let manifest_role = entries[0]
            .metadata
            .get("evidence_role")
            .and_then(Value::as_str);
        if manifest_role != link.evidence_role.as_deref() {
            return Err(replay_error(
                "replay_dependency_mismatch",
                "evidence link role does not match its manifest dependency",
            ));
        }
        if link.supports_label && !supporting_pairs.insert(pair) {
            return Err(replay_error(
                "replay_dependency_ambiguous",
                "claim evidence support link is duplicated",
            ));
        }
    }
    if supporting_pairs != expected_supporting_pairs {
        return Err(replay_error(
            "replay_dependency_mismatch",
            "supporting evidence links do not match claim dependencies",
        ));
    }
    for (path, expected_hash) in verified_artifacts {
        let current_hash = sha256_file(&path)
            .map_err(|message| replay_error("replay_snapshot_changed", message))?;
        if current_hash != expected_hash {
            return Err(replay_error(
                "replay_snapshot_changed",
                "artifact bytes changed during replay verification",
            ));
        }
    }
    let reloaded = load_persisted_ledger(root, &request.run_id)
        .map_err(|(_, message, _)| replay_error("replay_snapshot_changed", message))?;
    verify_ledger(&reloaded)
        .map_err(|(_, message, _)| replay_error("replay_snapshot_changed", message))?;
    let stable = reloaded.commits.last().map(|c| c.commit_hash.as_str()) == Some(tip)
        && reloaded.commits.len() == ledger.commits.len();
    if !stable {
        return Err(replay_error(
            "replay_snapshot_changed",
            "ledger changed during replay verification",
        ));
    }
    Ok(map_value([
        ("run_id", Value::String(request.run_id)),
        ("ledger_tip_hash", Value::String(tip.to_owned())),
        (
            "ledger_commit_count",
            Value::Number((ledger.commits.len() as u64).into()),
        ),
        (
            "ledger_artifact_count",
            Value::Number((paths.len() as u64).into()),
        ),
        (
            "ledger_artifact_inventory_hash",
            Value::String(sha256_hex(inventory_json.as_bytes())),
        ),
        (
            "required_outputs_checked",
            Value::Number((REPLAY_REQUIRED_OUTPUTS.len() as u64).into()),
        ),
        ("manifest_artifact_id", Value::String(manifest_artifact.id)),
        (
            "manifest_producing_commit_hash",
            Value::String(manifest_commit.commit_hash.clone()),
        ),
        (
            "manifest_entry_count",
            Value::Number((manifest.artifacts.len() as u64).into()),
        ),
        (
            "manifest_inventory_hash",
            Value::String(manifest_inventory_hash),
        ),
        (
            "claims_checked",
            Value::Number((claim_table.claims.len() as u64).into()),
        ),
        (
            "claim_evidence_links_checked",
            Value::Number((claim_table.evidence_links.len() as u64).into()),
        ),
        ("core_replay_valid", Value::Bool(true)),
        ("ledger_snapshot_stable", Value::Bool(true)),
        ("authority_boundary_valid", Value::Bool(true)),
        ("authority_granted", Value::Bool(false)),
    ]))
}

fn validate_checkpoint_index(
    index: &CheckpointIndexWire,
    run_id: &str,
    index_artifact_id: &str,
) -> Result<(), KernelOperationError> {
    if index.run_id != run_id
        || index.checkpoint_count == 0
        || index.checkpoint_count != index.checkpoints.len()
        || index.checkpoints.windows(2).any(|pair| pair[0] == pair[1])
        || index.checkpoints.iter().collect::<HashSet<_>>().len() != index.checkpoints.len()
        || index.latest_controller_run_id.is_empty()
        || index.latest_completed_stage.is_none()
        || index_artifact_id
            != format!(
                "autonomous-paper-checkpoint-index-{:04}",
                index.checkpoint_count
            )
    {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint index identity or count is invalid".to_owned(),
            Some("payload.index"),
        ));
    }
    for (position, path) in index.checkpoints.iter().enumerate() {
        let number = position + 1;
        let prefix = format!("runs/{run_id}/reports/autonomous-paper-checkpoint-{number:04}-");
        if !path.starts_with(&prefix) || !path.ends_with(".json") || path.contains("..") {
            return Err((
                "checkpoint_index_invalid",
                "checkpoint inventory contains a non-canonical path".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
    }
    Ok(())
}

fn checkpoint_commit_refs(
    commit: &WireLedgerCommit,
    run_id: &str,
    expected_number: Option<usize>,
    expected_checkpoint_path: Option<&str>,
) -> Result<(WireArtifactRef, WireArtifactRef), KernelOperationError> {
    if commit.run_id != run_id || commit.artifact_refs.len() != 2 {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint commit must contain exactly two artifacts for the declared run".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    }
    let refs: Vec<WireArtifactRef> = commit
        .artifact_refs
        .iter()
        .map(|raw| {
            serde_json::from_value(raw.clone()).map_err(|error| {
                (
                    "checkpoint_index_invalid",
                    format!("checkpoint artifact reference is invalid: {error}"),
                    Some("payload.index.producing_commit_hash"),
                )
            })
        })
        .collect::<Result<_, _>>()?;
    let index_position = refs.iter().position(|artifact| {
        artifact.artifact_type == "report"
            && artifact
                .id
                .starts_with("autonomous-paper-checkpoint-index-")
    });
    let checkpoint_position = refs.iter().position(|artifact| {
        artifact.artifact_type == "report"
            && artifact.id.starts_with("autonomous-paper-checkpoint-")
            && !artifact.id.contains("-index-")
    });
    let (Some(index_position), Some(checkpoint_position)) = (index_position, checkpoint_position)
    else {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint commit must contain one checkpoint and one index report".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    };
    let index_ref = refs[index_position].clone();
    let checkpoint_ref = refs[checkpoint_position].clone();
    if let Some(number) = expected_number {
        if index_ref.id != format!("autonomous-paper-checkpoint-index-{number:04}") {
            return Err((
                "checkpoint_index_invalid",
                "checkpoint commit index number is not sequential".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
    }
    if index_ref.path != format!("runs/{run_id}/reports/{}.json", index_ref.id) {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint index path is not canonical for its artifact id".to_owned(),
            Some("payload.index"),
        ));
    }
    if let Some(path) = expected_checkpoint_path {
        if checkpoint_ref.path != path {
            return Err((
                "checkpoint_index_invalid",
                "checkpoint commit does not link the declared checkpoint path".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
    }
    Ok((index_ref, checkpoint_ref))
}

fn validate_checkpoint_artifact_ref(
    artifact: &WireArtifactRef,
    run_id: &str,
    commit: &WireLedgerCommit,
) -> Result<(), KernelOperationError> {
    validate_artifact_ref(&serde_json::to_value(artifact).map_err(|error| {
        (
            "checkpoint_index_invalid",
            error.to_string(),
            Some("payload.index.producing_commit_hash"),
        )
    })?)
    .map_err(|message| ("checkpoint_index_invalid", message, Some("payload.index")))?;
    validate_artifact_location(run_id, artifact)
        .map_err(|message| ("checkpoint_index_invalid", message, Some("payload.index")))?;
    if checkpoint_metadata_claims_authority(&artifact.metadata) {
        return Err((
            "checkpoint_authority_violation",
            "checkpoint artifact metadata claims forbidden authority".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    }
    if artifact.producing_commit_hash.as_deref() != Some(commit.commit_hash.as_str())
        || artifact.metadata != checkpoint_metadata()
        || !is_sha256_hex(&artifact.content_hash)
    {
        return Err((
            "checkpoint_index_invalid",
            "checkpoint artifact reference has invalid metadata, hash, or producer link".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    }
    Ok(())
}

fn validate_checkpoint_commit_payload(
    commit: &WireLedgerCommit,
    checkpoint: &CheckpointWire,
) -> Result<(), KernelOperationError> {
    let expected = map_value([
        (
            "checkpoint_hash",
            Value::String(checkpoint.checkpoint_hash.clone()),
        ),
        (
            "controller_run_id",
            Value::String(checkpoint.controller_run_id.clone()),
        ),
        ("creates_scientific_validation", Value::Bool(false)),
        ("implies_publication_readiness", Value::Bool(false)),
        ("is_verification_evidence", Value::Bool(false)),
        ("publication_ready", Value::Bool(false)),
        ("run_id", Value::String(checkpoint.run_id.clone())),
        ("stage_name", Value::String(checkpoint.stage_name.clone())),
    ]);
    if commit.payload != expected {
        return Err((
            "checkpoint_record_invalid",
            "checkpoint commit payload is not the exact frozen payload".to_owned(),
            Some("payload.index.producing_commit_hash"),
        ));
    }
    Ok(())
}

fn checkpoint_metadata() -> Map<String, Value> {
    map_value([
        (
            "artifact_role",
            Value::String("controller_reliability_context".to_owned()),
        ),
        ("creates_scientific_validation", Value::Bool(false)),
        ("format", Value::String("json".to_owned())),
        ("implies_publication_readiness", Value::Bool(false)),
        ("is_verification_evidence", Value::Bool(false)),
        ("publication_ready", Value::Bool(false)),
        (
            "stage",
            Value::String("autonomous_paper_checkpoint".to_owned()),
        ),
    ])
}

fn checkpoint_metadata_claims_authority(metadata: &Map<String, Value>) -> bool {
    [
        "publication_ready",
        "creates_scientific_validation",
        "implies_publication_readiness",
        "is_verification_evidence",
    ]
    .iter()
    .any(|field| metadata.get(*field) == Some(&Value::Bool(true)))
}

fn validate_checkpoint_index_snapshot(
    root: &Path,
    run_id: &str,
    artifact: &WireArtifactRef,
    expected_paths: &[String],
    expected_count: usize,
    checkpoint: &CheckpointWire,
) -> Result<(), KernelOperationError> {
    let path = resolve_run_file(root, run_id, &artifact.path).map_err(|message| {
        (
            "checkpoint_index_invalid",
            message,
            Some("payload.index.checkpoints"),
        )
    })?;
    if sha256_file(&path).map_err(|message| {
        (
            "checkpoint_index_invalid",
            message,
            Some("payload.index.checkpoints"),
        )
    })? != artifact.content_hash
    {
        return Err((
            "artifact_hash_mismatch",
            "historical checkpoint index bytes do not match the ledger hash".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    let value = read_json_without_duplicate_keys(&path).map_err(|message| {
        (
            "checkpoint_index_invalid",
            message,
            Some("payload.index.checkpoints"),
        )
    })?;
    let snapshot: CheckpointIndexWire =
        parse_closed(&value, "payload.index.checkpoints").map_err(|(_, message, _)| {
            (
                "checkpoint_index_invalid",
                message,
                Some("payload.index.checkpoints"),
            )
        })?;
    if snapshot.publication_ready
        || snapshot.creates_scientific_validation
        || snapshot.implies_publication_readiness
        || snapshot.is_verification_evidence
    {
        return Err((
            "checkpoint_authority_violation",
            "historical checkpoint index claims forbidden authority".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    if snapshot.run_id != run_id
        || snapshot.checkpoint_count != expected_count
        || snapshot.checkpoints != expected_paths
        || snapshot.latest_controller_run_id != checkpoint.controller_run_id
        || snapshot.latest_completed_stage.as_deref() != Some(checkpoint.stage_name.as_str())
        || snapshot.resume_allowed
            != checkpoint_is_reusable(checkpoint).map_err(|message| {
                (
                    "checkpoint_record_invalid",
                    message,
                    Some("payload.index.checkpoints"),
                )
            })?
        || snapshot.resume_blockers
            != if checkpoint_is_reusable(checkpoint).map_err(|message| {
                (
                    "checkpoint_record_invalid",
                    message,
                    Some("payload.index.checkpoints"),
                )
            })? {
                Vec::new()
            } else {
                vec![checkpoint.stage_name.clone()]
            }
    {
        return Err((
            "checkpoint_index_invalid",
            "historical checkpoint index is not the exact declared prefix".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    Ok(())
}

fn validate_checkpoint_record(
    root: &Path,
    run_id: &str,
    commit: &WireLedgerCommit,
    checkpoint: &CheckpointWire,
    previous_hash: Option<&str>,
    number: usize,
) -> Result<(), KernelOperationError> {
    if checkpoint.run_id != run_id
        || checkpoint.controller_run_id.is_empty()
        || !matches!(
            checkpoint.stage_name.as_str(),
            "base_generation"
                | "autonomous_loop"
                | "final_manuscript_regeneration"
                | "final_release_bundle_assembly"
                | "final_bundle_verification"
                | "handoff"
        )
        || checkpoint.stage_status.is_empty()
        || checkpoint.stage_started_at.is_empty()
        || checkpoint.stage_completed_at.is_empty()
        || !is_sha256_hex(&checkpoint.checkpoint_hash)
        || !matches!(
            checkpoint.safety_gate_status.as_str(),
            "passed" | "passed_with_warnings" | "failed"
        )
        || !matches!(
            checkpoint.verification_status.as_str(),
            "verified" | "verified_with_warnings" | "failed" | "unverified"
        )
    {
        return Err((
            "checkpoint_record_invalid",
            "checkpoint record has invalid identity, protocol, status, or authority fields"
                .to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    if checkpoint.protocol_version != PROTOCOL_VERSION {
        return Err((
            "checkpoint_protocol_mismatch",
            "checkpoint protocol version does not match the kernel protocol".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    if checkpoint.publication_ready
        || checkpoint.creates_scientific_validation
        || checkpoint.implies_publication_readiness
        || checkpoint.is_verification_evidence
    {
        return Err((
            "checkpoint_authority_violation",
            "checkpoint claims forbidden scientific or publication authority".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    if checkpoint.ledger_tip_hash_optional.as_deref() != commit.parent_hash.as_deref() {
        return Err((
            "checkpoint_ledger_mismatch",
            "checkpoint ledger tip does not equal its producing commit parent".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    if number == 1 {
        if !checkpoint.input_hashes.is_empty() {
            return Err((
                "checkpoint_chain_mismatch",
                "the first checkpoint must not have predecessor inputs".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
    } else if checkpoint.input_hashes.len() != 1
        || checkpoint
            .input_hashes
            .get("previous_checkpoint")
            .map(String::as_str)
            != previous_hash
    {
        return Err((
            "checkpoint_chain_mismatch",
            "checkpoint predecessor input does not match the prior checkpoint".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    let mut canonical = serde_json::to_value(checkpoint).map_err(|error| {
        (
            "checkpoint_record_invalid",
            error.to_string(),
            Some("payload.index.checkpoints"),
        )
    })?;
    canonical
        .as_object_mut()
        .expect("checkpoint wire is an object")
        .remove("checkpoint_hash");
    let expected_hash = canonical_json(&canonical)
        .map(|value| sha256_hex(value.as_bytes()))
        .map_err(|error| {
            (
                "checkpoint_record_invalid",
                error.to_string(),
                Some("payload"),
            )
        })?;
    if checkpoint.checkpoint_hash != expected_hash {
        return Err((
            "checkpoint_hash_mismatch",
            "checkpoint self-hash does not match canonical checkpoint content".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    if checkpoint.stage_artifact_paths.len() != checkpoint.output_hashes.len()
        || checkpoint
            .stage_artifact_paths
            .iter()
            .any(|path| !checkpoint.output_hashes.contains_key(path))
        || checkpoint.output_hashes.keys().any(|path| {
            !checkpoint
                .stage_artifact_paths
                .iter()
                .any(|candidate| candidate == path)
        })
    {
        return Err((
            "checkpoint_record_invalid",
            "checkpoint stage paths and output hashes differ".to_owned(),
            Some("payload.index.checkpoints"),
        ));
    }
    for (relative, expected_hash) in &checkpoint.output_hashes {
        if !is_sha256_hex(expected_hash) {
            return Err((
                "checkpoint_output_hash_mismatch",
                "checkpoint output hash is not a lowercase SHA-256 digest".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
        let path = resolve_run_file(root, run_id, relative).map_err(|message| {
            let code = if message.contains("does not resolve to an existing file") {
                "checkpoint_output_missing"
            } else {
                "checkpoint_output_path_invalid"
            };
            (code, message, Some("payload.index.checkpoints"))
        })?;
        if sha256_file(&path).map_err(|message| {
            (
                "checkpoint_output_missing",
                message,
                Some("payload.index.checkpoints"),
            )
        })? != *expected_hash
        {
            return Err((
                "checkpoint_output_hash_mismatch",
                "checkpoint output bytes do not match the declared hash".to_owned(),
                Some("payload.index.checkpoints"),
            ));
        }
    }
    Ok(())
}

fn checkpoint_is_reusable(checkpoint: &CheckpointWire) -> Result<bool, String> {
    let reusable = matches!(
        checkpoint.safety_gate_status.as_str(),
        "passed" | "passed_with_warnings"
    ) && matches!(
        checkpoint.stage_status.as_str(),
        "completed" | "completed_with_warnings" | "reused"
    ) && checkpoint.verified_for_resume
        && matches!(
            checkpoint.verification_status.as_str(),
            "verified" | "verified_with_warnings"
        )
        && checkpoint.verification_errors.is_empty();
    let terminal_failure = checkpoint.safety_gate_status == "failed"
        && matches!(checkpoint.stage_status.as_str(), "blocked" | "failed")
        && !checkpoint.verified_for_resume
        && checkpoint.verification_status == "failed";
    if !reusable && !terminal_failure {
        return Err(
            "checkpoint status is neither reusable nor a coherent terminal failure".to_owned(),
        );
    }
    Ok(reusable)
}

fn read_json_without_duplicate_keys(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|_| "artifact JSON cannot be read".to_owned())?;
    let text =
        std::str::from_utf8(&bytes).map_err(|_| "artifact JSON is not valid UTF-8".to_owned())?;
    parse_json_without_duplicate_keys(text).map_err(|error| error.to_string())
}

fn parse_closed<T: serde::de::DeserializeOwned>(
    value: &Value,
    path: &'static str,
) -> Result<T, (&'static str, String, Option<&'static str>)> {
    serde_json::from_value(value.clone()).map_err(|error| {
        (
            "protocol_invalid",
            format!("closed bundle schema rejected member: {error}"),
            Some(path),
        )
    })
}

fn sha256_canonical(value: &Value) -> Result<String, (&'static str, String, Option<&'static str>)> {
    let canonical = canonical_json(value).map_err(|error| {
        (
            "protocol_invalid",
            error.to_string(),
            Some("payload.bundle"),
        )
    })?;
    Ok(sha256_hex(canonical.as_bytes()))
}

fn contains_network_marker(value: &str) -> bool {
    let lowered = value.to_lowercase();
    ["http://", "https://", "ftp://", "curl ", "wget "]
        .iter()
        .any(|marker| lowered.contains(marker))
}

fn is_fake_backend(value: &str) -> bool {
    value.eq_ignore_ascii_case("fake")
}

fn contains_network_or_absolute_marker(value: &Value) -> bool {
    fn contains_absolute_marker(text: &str) -> bool {
        let lowered = text.to_lowercase();
        lowered.trim().starts_with("~/")
            || lowered.trim().starts_with('\\')
            || lowered.contains("file:///")
            || format!(" {lowered}").contains(" /")
            || lowered.contains("\\\\")
    }

    fn visit(value: &Value) -> bool {
        match value {
            Value::String(text) => contains_network_marker(text) || contains_absolute_marker(text),
            Value::Array(items) => items.iter().any(visit),
            Value::Object(object) => object.iter().any(|(key, value)| {
                contains_network_marker(key) || contains_absolute_marker(key) || visit(value)
            }),
            Value::Null | Value::Bool(_) | Value::Number(_) => false,
        }
    }

    visit(value)
}

fn contains_required_forbidden_inputs(inputs: &[String]) -> bool {
    [
        "PublicDownload",
        "UserProvided",
        "RealWorldData",
        "network",
        "absolute_path",
    ]
    .iter()
    .all(|required| inputs.iter().any(|input| input == required))
}

fn synthetic_input_projection(contract: &SyntheticContractWire) -> Value {
    map_value([
        ("candidate_id", Value::String(contract.candidate_id.clone())),
        ("claim_id", Value::String(contract.claim_id.clone())),
        (
            "experiment_id",
            Value::String(contract.experiment_id.clone()),
        ),
        (
            "experiment_kind",
            Value::String(contract.experiment_kind.clone()),
        ),
        ("data_regime", Value::String(contract.data_regime.clone())),
        (
            "synthetic_data_spec",
            Value::Object(contract.synthetic_data_spec.clone()),
        ),
        ("model_spec", Value::Object(contract.model_spec.clone())),
        (
            "algorithm_spec",
            Value::Object(contract.algorithm_spec.clone()),
        ),
        (
            "metrics",
            Value::Array(
                contract
                    .metrics
                    .iter()
                    .cloned()
                    .map(Value::String)
                    .collect(),
            ),
        ),
        (
            "acceptance_criteria",
            Value::Object(contract.acceptance_criteria.clone()),
        ),
        ("random_seed", Value::Number(contract.random_seed.into())),
        ("replications", Value::Number(contract.replications.into())),
    ])
    .into()
}

fn acceptance_satisfied(metrics: &Map<String, Value>, criteria: &Map<String, Value>) -> bool {
    if criteria.is_empty() {
        return false;
    }
    criteria.iter().all(|(name, rule)| {
        let Some(value) = metrics.get(name).and_then(Value::as_f64) else {
            return false;
        };
        if !value.is_finite() {
            return false;
        }
        match rule {
            Value::Object(rule) => {
                if rule.is_empty() || rule.keys().any(|key| key != "min" && key != "max") {
                    return false;
                }
                if !rule.contains_key("min") && !rule.contains_key("max") {
                    return false;
                }
                let min = rule
                    .get("min")
                    .map(|bound| bound.as_f64().filter(|bound| bound.is_finite()));
                let max = rule
                    .get("max")
                    .map(|bound| bound.as_f64().filter(|bound| bound.is_finite()));
                let min = match min {
                    None => f64::NEG_INFINITY,
                    Some(Some(bound)) => bound,
                    Some(None) => return false,
                };
                let max = match max {
                    None => f64::INFINITY,
                    Some(Some(bound)) => bound,
                    Some(None) => return false,
                };
                if min > max {
                    return false;
                }
                value >= min && value <= max
            }
            Value::Number(number) => number
                .as_f64()
                .is_some_and(|minimum| minimum.is_finite() && value >= minimum),
            _ => false,
        }
    })
}

fn validate_ledger_payload_shape(payload: &Map<String, Value>) -> Result<(), String> {
    if payload.len() != 2 || !payload.contains_key("run_id") || !payload.contains_key("commits") {
        return Err(
            "ledger.verify payload must contain exactly run_id and commits fields".to_owned(),
        );
    }
    let run_id = payload
        .get("run_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "run_id must be a string".to_owned())?;
    if run_id.is_empty() {
        return Err("run_id must not be empty".to_owned());
    }
    if !payload.get("commits").is_some_and(Value::is_array) {
        return Err("commits must be an array".to_owned());
    }
    Ok(())
}

fn validate_ledger_payload_structure(payload: &Map<String, Value>) -> Result<(), String> {
    validate_ledger_payload_shape(payload)?;
    let ledger = serde_json::from_value::<LedgerVerifyPayload>(Value::Object(payload.clone()))
        .map_err(|error| error.to_string())?;
    for commit in &ledger.commits {
        validate_wire_commit_structure(commit)?;
    }
    Ok(())
}

fn validate_wire_commit_structure(commit: &WireLedgerCommit) -> Result<(), String> {
    if !is_sha256_hex(&commit.commit_hash) {
        return Err("commit_hash must be a lowercase SHA-256 hex digest".to_owned());
    }
    if commit
        .parent_hash
        .as_deref()
        .is_some_and(|parent_hash| !is_sha256_hex(parent_hash))
    {
        return Err("parent_hash must be null or a lowercase SHA-256 hex digest".to_owned());
    }
    if commit.run_id.is_empty() {
        return Err("commit run_id must not be empty".to_owned());
    }
    if !is_valid_action_type(&commit.action_type) {
        return Err(format!("unknown action_type: {}", commit.action_type));
    }
    if commit.timestamp.is_empty() {
        return Err("timestamp must not be empty".to_owned());
    }
    for artifact in &commit.artifact_refs {
        validate_artifact_ref(artifact)?;
    }
    Ok(())
}

fn verify_ledger_payload(
    payload: &Map<String, Value>,
) -> Result<Map<String, Value>, (&'static str, String, Option<&'static str>)> {
    validate_ledger_payload_shape(payload)
        .map_err(|message| ("protocol_invalid", message, Some("payload")))?;
    let ledger: LedgerVerifyPayload = serde_json::from_value(Value::Object(payload.clone()))
        .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;
    verify_ledger(&ledger)
}

fn verify_ledger(
    ledger: &LedgerVerifyPayload,
) -> Result<Map<String, Value>, (&'static str, String, Option<&'static str>)> {
    let mut seen = HashSet::new();
    let mut previous_hash = None;
    let mut root_hash = None;
    let mut tip_hash = None;
    let mut produced_artifacts: HashMap<(String, String), ArtifactSignature> = HashMap::new();
    for (index, commit) in ledger.commits.iter().enumerate() {
        if commit.run_id != ledger.run_id {
            return Err((
                "ledger_cross_run_parent",
                format!("commit belongs to a different run: {}", commit.run_id),
                Some("payload.commits.run_id"),
            ));
        }
        if !is_sha256_hex(&commit.commit_hash) {
            return Err((
                "hash_invalid",
                "commit_hash must be a lowercase SHA-256 hex digest".to_owned(),
                Some("payload.commits.commit_hash"),
            ));
        }
        if !is_valid_action_type(&commit.action_type) {
            return Err((
                "protocol_invalid",
                format!("unknown action_type: {}", commit.action_type),
                Some("payload.commits.action_type"),
            ));
        }
        if commit.timestamp.is_empty() {
            return Err((
                "protocol_invalid",
                "timestamp must not be empty".to_owned(),
                Some("payload.commits.timestamp"),
            ));
        }
        if let Some(parent_hash) = &commit.parent_hash {
            if !is_sha256_hex(parent_hash) {
                return Err((
                    "hash_invalid",
                    "parent_hash must be a lowercase SHA-256 hex digest".to_owned(),
                    Some("payload.commits.parent_hash"),
                ));
            }
            if !seen.contains(parent_hash) {
                return Err((
                    "ledger_parent_missing",
                    format!("missing or out-of-order parent hash: {parent_hash}"),
                    Some("payload.commits.parent_hash"),
                ));
            }
        }
        if index == 0 {
            if commit.parent_hash.is_some() {
                return Err((
                    "ledger_multiple_roots",
                    "the first commit must not have a parent hash".to_owned(),
                    Some("payload.commits.parent_hash"),
                ));
            }
        } else if commit.parent_hash.as_deref() != previous_hash.as_deref() {
            return Err((
                "ledger_non_tip_append",
                "each commit after the root must point to the immediately preceding commit"
                    .to_owned(),
                Some("payload.commits.parent_hash"),
            ));
        }
        let mut artifact_ids = HashSet::new();
        for artifact in &commit.artifact_refs {
            validate_artifact_ref(artifact).map_err(|message| {
                (
                    "protocol_invalid",
                    message,
                    Some("payload.commits.artifact_refs"),
                )
            })?;
            let artifact_object = artifact.as_object().expect("validated artifact object");
            let artifact_id = artifact_object
                .get("id")
                .and_then(Value::as_str)
                .expect("validated artifact id");
            if !artifact_ids.insert(artifact_id.to_owned()) {
                return Err((
                    "ledger_artifact_duplicate",
                    format!("duplicate artifact id in commit: {artifact_id}"),
                    Some("payload.commits.artifact_refs.id"),
                ));
            }
            let signature = ArtifactSignature::from_object(artifact_object)?;
            if let Some(producing_hash) = artifact_object
                .get("producing_commit_hash")
                .and_then(Value::as_str)
            {
                if producing_hash != commit.commit_hash && !seen.contains(producing_hash) {
                    return Err((
                        "ledger_artifact_commit_missing",
                        format!("artifact producer commit is not in this ledger: {producing_hash}"),
                        Some("payload.commits.artifact_refs.producing_commit_hash"),
                    ));
                }
                if producing_hash != commit.commit_hash {
                    let key = (producing_hash.to_owned(), artifact_id.to_owned());
                    if produced_artifacts.get(&key) != Some(&signature) {
                        return Err((
                            "ledger_artifact_commit_mismatch",
                            format!("artifact {artifact_id} does not match its producing commit"),
                            Some("payload.commits.artifact_refs"),
                        ));
                    }
                }
            }
            if artifact_object
                .get("producing_commit_hash")
                .and_then(Value::as_str)
                == Some(commit.commit_hash.as_str())
            {
                produced_artifacts.insert(
                    (commit.commit_hash.clone(), artifact_id.to_owned()),
                    signature,
                );
            }
        }
        let recomputed = compute_wire_commit_hash(commit)
            .map_err(|message| ("protocol_invalid", message, Some("payload.commits")))?;
        if recomputed != commit.commit_hash {
            return Err((
                "ledger_hash_mismatch",
                format!("commit hash mismatch: {}", commit.commit_hash),
                Some("payload.commits.commit_hash"),
            ));
        }
        if !seen.insert(commit.commit_hash.clone()) {
            return Err((
                "ledger_fork",
                format!("duplicate commit hash: {}", commit.commit_hash),
                Some("payload.commits.commit_hash"),
            ));
        }
        if index == 0 {
            root_hash = Some(commit.commit_hash.clone());
        }
        tip_hash = Some(commit.commit_hash.clone());
        previous_hash = Some(commit.commit_hash.clone());
    }
    Ok(map_value([
        ("valid", Value::Bool(true)),
        ("run_id", Value::String(ledger.run_id.clone())),
        (
            "commit_count",
            Value::Number((ledger.commits.len() as u64).into()),
        ),
        ("root_hash", root_hash.map_or(Value::Null, Value::String)),
        ("tip_hash", tip_hash.map_or(Value::Null, Value::String)),
    ]))
}

fn verify_artifact_payload(
    payload: &Map<String, Value>,
    project_root: Option<&Path>,
) -> Result<Map<String, Value>, (&'static str, String, Option<&'static str>)> {
    validate_artifact_payload_shape(payload)
        .map_err(|message| ("protocol_invalid", message, Some("payload")))?;
    let verify: ArtifactVerifyPayload = serde_json::from_value(Value::Object(payload.clone()))
        .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;
    validate_artifact_ref(&serde_json::to_value(&verify.artifact).map_err(|error| {
        (
            "protocol_invalid",
            error.to_string(),
            Some("payload.artifact"),
        )
    })?)
    .map_err(|message| ("protocol_invalid", message, Some("payload.artifact")))?;
    validate_artifact_location(&verify.run_id, &verify.artifact).map_err(|message| {
        (
            "artifact_path_invalid",
            message,
            Some("payload.artifact.path"),
        )
    })?;
    if is_presentation_artifact(&verify.artifact)
        && verify
            .artifact
            .metadata
            .get("is_verification_evidence")
            .is_some_and(|value| value == &Value::Bool(true))
    {
        return Err((
            "artifact_presentation_override",
            "presentation artifact metadata cannot mark the artifact as verification evidence"
                .to_owned(),
            Some("payload.artifact.metadata.is_verification_evidence"),
        ));
    }
    if is_verification_evidence(&verify.artifact) && verify.artifact.producing_commit_hash.is_none()
    {
        return Err((
            "artifact_producer_missing",
            "verification evidence must link to a producing commit".to_owned(),
            Some("payload.artifact.producing_commit_hash"),
        ));
    }
    let project_root = project_root.ok_or((
        "kernel_root_missing",
        "artifact verification requires --root <project-root>".to_owned(),
        None,
    ))?;
    if let Some(expected_hash) = verify.artifact.producing_commit_hash.as_deref() {
        let ledger = load_persisted_ledger(project_root, &verify.run_id)?;
        verify_ledger(&ledger)?;
        let commit = ledger
            .commits
            .iter()
            .find(|commit| commit.commit_hash == expected_hash)
            .ok_or((
                "artifact_producer_missing",
                "artifact producer commit is absent from the persisted run ledger".to_owned(),
                Some("payload.artifact.producing_commit_hash"),
            ))?;
        let matches_artifact = commit.artifact_refs.iter().any(|candidate| {
            candidate
                .as_object()
                .is_some_and(|object| artifact_matches_reference(&verify.artifact, object))
        });
        if !matches_artifact {
            return Err((
                "artifact_producer_link_mismatch",
                "producer commit does not contain the exact artifact reference".to_owned(),
                Some("payload.artifact.producing_commit_hash"),
            ));
        }
    }
    let artifact_path = resolve_run_file(project_root, &verify.run_id, &verify.artifact.path)
        .map_err(|message| {
            (
                "artifact_path_invalid",
                message,
                Some("payload.artifact.path"),
            )
        })?;
    let actual_hash = sha256_file(&artifact_path).map_err(|message| {
        (
            "artifact_read_failed",
            message,
            Some("payload.artifact.path"),
        )
    })?;
    if actual_hash != verify.artifact.content_hash {
        return Err((
            "artifact_hash_mismatch",
            format!(
                "persisted artifact bytes hash to {actual_hash}, expected {}",
                verify.artifact.content_hash
            ),
            Some("payload.artifact.content_hash"),
        ));
    }
    Ok(map_value([
        ("valid", Value::Bool(true)),
        ("run_id", Value::String(verify.run_id)),
        ("artifact_id", Value::String(verify.artifact.id)),
        ("content_hash", Value::String(actual_hash)),
        (
            "producing_commit_hash",
            verify
                .artifact
                .producing_commit_hash
                .map_or(Value::Null, Value::String),
        ),
    ]))
}

fn classify_evidence_payload(
    payload: &Map<String, Value>,
    project_root: Option<&Path>,
    mode: KernelMode,
) -> Result<EvidenceClassification, KernelOperationError> {
    validate_artifact_payload_shape(payload)
        .map_err(|message| ("protocol_invalid", message, Some("payload")))?;
    let classify: EvidenceClassifyPayload = serde_json::from_value(Value::Object(payload.clone()))
        .map_err(|error| ("protocol_invalid", error.to_string(), Some("payload")))?;

    // Classification is deliberately downstream of the complete persisted artifact check. This
    // keeps a valid-looking role from bypassing bytes, provenance, or ledger verification.
    verify_artifact_payload(payload, project_root)?;

    let artifact = &classify.artifact;
    let explicit_false = artifact
        .metadata
        .get("is_verification_evidence")
        .is_some_and(|value| value == &Value::Bool(false));
    let explicit_true = artifact
        .metadata
        .get("is_verification_evidence")
        .is_some_and(|value| value == &Value::Bool(true));
    let role = artifact
        .metadata
        .get("evidence_role")
        .and_then(Value::as_str);

    if is_presentation_artifact(artifact) {
        return Ok((
            evidence_classification_result(
                &classify.run_id,
                &artifact.id,
                "Presentation",
                None,
                false,
            ),
            Vec::new(),
        ));
    }

    if explicit_false || artifact.artifact_type == "literature" {
        return Ok((
            evidence_classification_result(&classify.run_id, &artifact.id, "Context", None, false),
            Vec::new(),
        ));
    }

    if explicit_true && role.is_none() {
        return Err((
            "authority_denied",
            "explicit verification authority requires a supported evidence role".to_owned(),
            Some("payload.artifact.metadata.evidence_role"),
        ));
    }

    if role.is_none() {
        let authority_class = if artifact.artifact_type == "report" {
            "Presentation"
        } else {
            "Context"
        };
        return Ok((
            evidence_classification_result(
                &classify.run_id,
                &artifact.id,
                authority_class,
                None,
                false,
            ),
            Vec::new(),
        ));
    }

    let role = role.expect("checked above");
    match role {
        "proof" => {
            if artifact.artifact_type != "lean" {
                return Err((
                    "authority_denied",
                    "proof evidence role requires a lean artifact".to_owned(),
                    Some("payload.artifact.metadata.evidence_role"),
                ));
            }
            Ok((
                evidence_classification_result(
                    &classify.run_id,
                    &artifact.id,
                    "CapabilityCandidate",
                    Some("LeanProof"),
                    false,
                ),
                Vec::new(),
            ))
        }
        "synthetic_experiment" => {
            if artifact.artifact_type != "experiment" {
                return Err((
                    "authority_denied",
                    "synthetic_experiment evidence role requires an experiment artifact".to_owned(),
                    Some("payload.artifact.metadata.evidence_role"),
                ));
            }
            Ok((
                evidence_classification_result(
                    &classify.run_id,
                    &artifact.id,
                    "CapabilityCandidate",
                    Some("SyntheticExperiment"),
                    false,
                ),
                Vec::new(),
            ))
        }
        "fake_proof" => classify_fake_candidate(
            &classify.run_id,
            artifact,
            mode,
            "lean",
            "LeanProof",
            "fake proof evidence requires a lean artifact",
        ),
        "fake_synthetic_experiment" => classify_fake_candidate(
            &classify.run_id,
            artifact,
            mode,
            "experiment",
            "SyntheticExperiment",
            "fake synthetic-experiment evidence requires an experiment artifact",
        ),
        "real_data_experiment" => {
            if artifact.artifact_type != "experiment" {
                return Err((
                    "authority_denied",
                    "real_data_experiment evidence role requires an experiment artifact".to_owned(),
                    Some("payload.artifact.metadata.evidence_role"),
                ));
            }
            if mode == KernelMode::StrictProduction {
                return Err((
                    "data_regime_denied",
                    "real-data experiment authority is not constructible in this kernel version"
                        .to_owned(),
                    Some("payload.artifact.metadata.evidence_role"),
                ));
            }
            Ok((
                evidence_classification_result(
                    &classify.run_id,
                    &artifact.id,
                    "Context",
                    None,
                    false,
                ),
                Vec::new(),
            ))
        }
        _ if explicit_true => Err((
            "authority_denied",
            "artifact role cannot provide verification authority".to_owned(),
            Some("payload.artifact.metadata.evidence_role"),
        )),
        _ => Ok((
            evidence_classification_result(&classify.run_id, &artifact.id, "Context", None, false),
            Vec::new(),
        )),
    }
}

fn classify_fake_candidate(
    run_id: &str,
    artifact: &WireArtifactRef,
    mode: KernelMode,
    expected_type: &str,
    candidate_kind: &str,
    mismatch_message: &str,
) -> Result<EvidenceClassification, KernelOperationError> {
    if artifact.artifact_type != expected_type {
        return Err((
            "authority_denied",
            mismatch_message.to_owned(),
            Some("payload.artifact.metadata.evidence_role"),
        ));
    }
    if mode == KernelMode::DevelopmentCompatibility {
        return Ok((
            evidence_classification_result(
                run_id,
                &artifact.id,
                "CapabilityCandidate",
                Some(candidate_kind),
                true,
            ),
            Vec::new(),
        ));
    }
    Ok((
        evidence_classification_result(run_id, &artifact.id, "Context", None, false),
        vec![KernelDiagnostic {
            code: "fake_backend_denied".to_owned(),
            message: "fake evidence is retained as context in strict production mode".to_owned(),
            path: Some("payload.artifact.metadata.evidence_role".to_owned()),
        }],
    ))
}

fn evidence_classification_result(
    run_id: &str,
    artifact_id: &str,
    authority_class: &str,
    candidate_kind: Option<&str>,
    compatibility_only: bool,
) -> Map<String, Value> {
    map_value([
        ("run_id", Value::String(run_id.to_owned())),
        ("artifact_id", Value::String(artifact_id.to_owned())),
        ("authority_class", Value::String(authority_class.to_owned())),
        (
            "candidate_kind",
            candidate_kind.map_or(Value::Null, |kind| Value::String(kind.to_owned())),
        ),
        ("compatibility_only", Value::Bool(compatibility_only)),
        ("authority_granted", Value::Bool(false)),
    ])
}

fn is_verification_evidence(artifact: &WireArtifactRef) -> bool {
    if artifact
        .metadata
        .get("is_verification_evidence")
        .is_some_and(|value| value == &Value::Bool(false))
        || is_presentation_artifact(artifact)
    {
        return false;
    }
    matches!(
        artifact.artifact_type.as_str(),
        "candidate" | "score" | "report" | "literature" | "lean" | "experiment" | "log"
    )
}

fn is_manifest_evidence(artifact: &WireArtifactRef) -> bool {
    if !is_verification_evidence(artifact) {
        return false;
    }
    if artifact.artifact_type == "lean" {
        return artifact
            .metadata
            .get("evidence_role")
            .and_then(Value::as_str)
            .is_some_and(|role| matches!(role, "fake_proof" | "proof"));
    }
    if artifact.artifact_type == "experiment" {
        return artifact
            .metadata
            .get("evidence_role")
            .and_then(Value::as_str)
            .is_some_and(|role| {
                matches!(role, "fake_synthetic_experiment" | "synthetic_experiment")
            });
    }
    if artifact.artifact_type == "literature" {
        return true;
    }
    artifact
        .metadata
        .get("evidence_role")
        .and_then(Value::as_str)
        .is_some_and(|role| {
            matches!(
                role,
                "proof"
                    | "fake_proof"
                    | "fake_synthetic_experiment"
                    | "synthetic_experiment"
                    | "retrieval_evidence"
                    | "literature_evidence"
            )
        })
}

fn is_manifest_presentation(artifact: &WireArtifactRef, evidence: bool) -> bool {
    is_presentation_artifact(artifact) || (artifact.artifact_type == "report" && !evidence)
}

fn is_forbidden_derived_evidence(artifact: &WireArtifactRef) -> bool {
    if !matches!(artifact.artifact_type.as_str(), "report" | "latex") {
        return false;
    }
    let relative = artifact
        .path
        .split('/')
        .skip(2)
        .collect::<Vec<_>>()
        .join("/");
    let identity = format!("{} {relative}", artifact.id).to_ascii_lowercase();
    [
        "comparison",
        "diagnostic",
        "export-readiness",
        "final-paper",
        "full-paper",
        "hygiene",
        "manifest",
        "manuscript",
        "paper-assembly",
        "paper-skeleton",
        "readiness",
        "release",
        "replay",
        "research-object",
        "runtime-summary",
        "runtime_summary",
    ]
    .iter()
    .any(|marker| identity.contains(marker))
}

fn resolve_run_file(project_root: &Path, run_id: &str, relative: &str) -> Result<PathBuf, String> {
    let root = fs::canonicalize(project_root)
        .map_err(|_| "project root does not exist or cannot be resolved".to_owned())?;
    let relative = Path::new(relative);
    let mut candidate = root.clone();
    for component in relative.components() {
        let std::path::Component::Normal(component) = component else {
            return Err("artifact path contains a non-normal component".to_owned());
        };
        candidate.push(component);
        let metadata = fs::symlink_metadata(&candidate)
            .map_err(|_| "artifact path does not resolve to an existing file".to_owned())?;
        if metadata.file_type().is_symlink() {
            return Err("artifact path traverses a symbolic link".to_owned());
        }
    }
    let run_root = fs::canonicalize(root.join("runs").join(run_id))
        .map_err(|_| "run directory does not exist or cannot be resolved".to_owned())?;
    let resolved = fs::canonicalize(&candidate)
        .map_err(|_| "artifact path does not resolve to an existing file".to_owned())?;
    if !resolved.starts_with(&run_root) || !run_root.starts_with(&root) {
        return Err("artifact path escapes the configured run directory".to_owned());
    }
    if !fs::metadata(&resolved)
        .map_err(|_| "artifact metadata cannot be read".to_owned())?
        .is_file()
    {
        return Err("artifact path is not a regular file".to_owned());
    }
    Ok(resolved)
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|_| "artifact file cannot be opened".to_owned())?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| "artifact file cannot be read".to_owned())?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn load_persisted_ledger(
    project_root: &Path,
    run_id: &str,
) -> Result<LedgerVerifyPayload, (&'static str, String, Option<&'static str>)> {
    let relative = format!("runs/{run_id}/ledger.sqlite");
    let ledger_path = resolve_run_file(project_root, run_id, &relative).map_err(|message| {
        (
            "ledger_unreadable",
            message,
            Some("payload.artifact.producing_commit_hash"),
        )
    })?;
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let connection = Connection::open_with_flags(ledger_path, flags).map_err(|error| {
        (
            "ledger_unreadable",
            format!("persisted ledger cannot be opened read-only: {error}"),
            Some("payload.artifact.producing_commit_hash"),
        )
    })?;
    let mut statement = connection
        .prepare(
            "SELECT commit_hash, parent_hash, run_id, candidate_id, action_type, \
             payload_json, artifact_refs_json, timestamp \
             FROM commits WHERE run_id = ?1 ORDER BY rowid",
        )
        .map_err(|error| {
            (
                "ledger_unreadable",
                format!("persisted ledger schema cannot be queried: {error}"),
                Some("payload.artifact.producing_commit_hash"),
            )
        })?;
    let rows = statement
        .query_map([run_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, String>(6)?,
                row.get::<_, String>(7)?,
            ))
        })
        .map_err(|error| {
            (
                "ledger_unreadable",
                format!("persisted ledger rows cannot be read: {error}"),
                Some("payload.artifact.producing_commit_hash"),
            )
        })?;
    let mut commits = Vec::new();
    for row in rows {
        let (
            commit_hash,
            parent_hash,
            row_run_id,
            candidate_id,
            action_type,
            payload_json,
            artifact_refs_json,
            timestamp,
        ) = row.map_err(|error| {
            (
                "ledger_unreadable",
                format!("persisted ledger row is invalid: {error}"),
                Some("payload.artifact.producing_commit_hash"),
            )
        })?;
        let payload = parse_json_without_duplicate_keys(&payload_json)
            .map_err(|error| {
                (
                    "ledger_unreadable",
                    format!("persisted ledger payload JSON is invalid: {error}"),
                    Some("payload.artifact.producing_commit_hash"),
                )
            })?
            .as_object()
            .cloned()
            .ok_or((
                "ledger_unreadable",
                "persisted ledger payload must be an object".to_owned(),
                Some("payload.artifact.producing_commit_hash"),
            ))?;
        let artifact_refs = parse_json_without_duplicate_keys(&artifact_refs_json)
            .map_err(|error| {
                (
                    "ledger_unreadable",
                    format!("persisted ledger artifact JSON is invalid: {error}"),
                    Some("payload.artifact.producing_commit_hash"),
                )
            })?
            .as_array()
            .cloned()
            .ok_or((
                "ledger_unreadable",
                "persisted ledger artifact references must be an array".to_owned(),
                Some("payload.artifact.producing_commit_hash"),
            ))?;
        commits.push(WireLedgerCommit {
            commit_hash,
            parent_hash,
            run_id: row_run_id,
            candidate_id,
            action_type,
            payload,
            artifact_refs,
            timestamp,
        });
    }
    Ok(LedgerVerifyPayload {
        run_id: run_id.to_owned(),
        commits,
    })
}

fn validate_artifact_location(run_id: &str, artifact: &WireArtifactRef) -> Result<(), String> {
    if !is_safe_segment(run_id) {
        return Err("run_id must use only letters, digits, '.', '_' and '-'".to_owned());
    }
    if !is_safe_segment(&artifact.id) {
        return Err("artifact id must use only letters, digits, '.', '_' and '-'".to_owned());
    }
    if artifact.path.contains('\\') || artifact.path.starts_with('/') {
        return Err("artifact path must be a relative POSIX path".to_owned());
    }
    let parts: Vec<&str> = artifact.path.split('/').collect();
    if parts.len() != 4
        || parts
            .iter()
            .any(|part| part.is_empty() || *part == "." || *part == "..")
    {
        return Err("artifact path must be runs/<run_id>/<type-directory>/<filename>".to_owned());
    }
    if parts[0] != "runs" || parts[1] != run_id {
        return Err("artifact path is outside the declared run".to_owned());
    }
    let expected_directory = artifact_directory(&artifact.artifact_type)
        .ok_or_else(|| "artifact type is not supported".to_owned())?;
    let directory_matches = parts[2] == expected_directory
        || (artifact.artifact_type == "report" && parts[2] == "research_object");
    if !directory_matches {
        return Err(format!(
            "artifact type {} must be stored under runs/{run_id}/{expected_directory}",
            artifact.artifact_type
        ));
    }
    if !is_safe_filename(parts[3]) {
        return Err("artifact filename contains unsafe characters".to_owned());
    }
    Ok(())
}

fn is_safe_segment(value: &str) -> bool {
    let mut bytes = value.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn is_safe_filename(value: &str) -> bool {
    is_safe_segment(value) && value.contains('.')
}

fn artifact_directory(artifact_type: &str) -> Option<&'static str> {
    match artifact_type {
        "candidate" => Some("candidates"),
        "score" => Some("scores"),
        "report" => Some("reports"),
        "literature" => Some("literature"),
        "lean" => Some("lean"),
        "experiment" => Some("experiments"),
        "log" => Some("logs"),
        "latex" => Some("latex"),
        _ => None,
    }
}

fn is_presentation_artifact(artifact: &WireArtifactRef) -> bool {
    artifact.artifact_type == "latex"
        || artifact.path.rsplit_once('.').is_some_and(|(_, suffix)| {
            suffix.eq_ignore_ascii_case("md")
                || suffix.eq_ignore_ascii_case("markdown")
                || suffix.eq_ignore_ascii_case("tex")
                || suffix.eq_ignore_ascii_case("pdf")
        })
}

fn artifact_matches_reference(artifact: &WireArtifactRef, reference: &Map<String, Value>) -> bool {
    let reference_producer = reference
        .get("producing_commit_hash")
        .and_then(Value::as_str);
    let reference_metadata = reference
        .get("metadata")
        .cloned()
        .unwrap_or_else(|| Value::Object(Map::new()));

    reference.get("id").and_then(Value::as_str) == Some(artifact.id.as_str())
        && reference.get("path").and_then(Value::as_str) == Some(artifact.path.as_str())
        && reference.get("type").and_then(Value::as_str) == Some(artifact.artifact_type.as_str())
        && reference.get("content_hash").and_then(Value::as_str)
            == Some(artifact.content_hash.as_str())
        && reference_producer == artifact.producing_commit_hash.as_deref()
        && reference_metadata == Value::Object(artifact.metadata.clone())
}

#[derive(Debug, PartialEq, Eq)]
struct ArtifactSignature {
    path: String,
    artifact_type: String,
    content_hash: String,
}

impl ArtifactSignature {
    fn from_object(
        object: &Map<String, Value>,
    ) -> Result<Self, (&'static str, String, Option<&'static str>)> {
        Ok(Self {
            path: object
                .get("path")
                .and_then(Value::as_str)
                .ok_or((
                    "protocol_invalid",
                    "artifact path must be a string".to_owned(),
                    Some("payload.commits.artifact_refs.path"),
                ))?
                .to_owned(),
            artifact_type: object
                .get("type")
                .and_then(Value::as_str)
                .ok_or((
                    "protocol_invalid",
                    "artifact type must be a string".to_owned(),
                    Some("payload.commits.artifact_refs.type"),
                ))?
                .to_owned(),
            content_hash: object
                .get("content_hash")
                .and_then(Value::as_str)
                .ok_or((
                    "protocol_invalid",
                    "artifact content_hash must be a string".to_owned(),
                    Some("payload.commits.artifact_refs.content_hash"),
                ))?
                .to_owned(),
        })
    }
}

fn compute_wire_commit_hash(commit: &WireLedgerCommit) -> Result<String, String> {
    let mut artifact_refs = Vec::with_capacity(commit.artifact_refs.len());
    for artifact in &commit.artifact_refs {
        let mut artifact = artifact
            .as_object()
            .ok_or_else(|| "artifact reference must be an object".to_owned())?
            .clone();
        if artifact
            .get("producing_commit_hash")
            .and_then(Value::as_str)
            == Some(commit.commit_hash.as_str())
        {
            artifact.insert(
                "producing_commit_hash".to_owned(),
                Value::String("<self>".to_owned()),
            );
        }
        artifact
            .entry("producing_commit_hash".to_owned())
            .or_insert(Value::Null);
        artifact
            .entry("metadata".to_owned())
            .or_insert_with(|| Value::Object(Map::new()));
        artifact_refs.push(Value::Object(artifact));
    }
    let value = map_value([
        (
            "parent_hash",
            commit
                .parent_hash
                .clone()
                .map_or(Value::Null, Value::String),
        ),
        ("run_id", Value::String(commit.run_id.clone())),
        (
            "candidate_id",
            commit
                .candidate_id
                .clone()
                .map_or(Value::Null, Value::String),
        ),
        ("action_type", Value::String(commit.action_type.clone())),
        ("payload", Value::Object(commit.payload.clone())),
        ("artifact_refs", Value::Array(artifact_refs)),
        ("timestamp", Value::String(commit.timestamp.clone())),
    ]);
    let canonical = canonical_json(&Value::Object(value)).map_err(|error| error.to_string())?;
    Ok(sha256_hex(canonical.as_bytes()))
}

fn validate_artifact_ref(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "artifact reference must be an object".to_owned())?;
    for key in object.keys() {
        if !matches!(
            key.as_str(),
            "id" | "type" | "path" | "content_hash" | "producing_commit_hash" | "metadata"
        ) {
            return Err(format!("unknown artifact reference field: {key}"));
        }
    }
    for key in ["id", "type", "path", "content_hash"] {
        if !object.contains_key(key) {
            return Err(format!("artifact reference missing field: {key}"));
        }
        if !object.get(key).is_some_and(Value::is_string) {
            return Err(format!("artifact reference field must be a string: {key}"));
        }
    }
    if object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .is_empty()
        || object
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .is_empty()
    {
        return Err("artifact reference id and path must not be empty".to_owned());
    }
    if !is_sha256_hex(
        object
            .get("content_hash")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    ) {
        return Err("artifact content_hash must be a lowercase SHA-256 hex digest".to_owned());
    }
    if let Some(value) = object.get("producing_commit_hash") {
        if !value.is_null() && !is_sha256_hex(value.as_str().unwrap_or_default()) {
            return Err(
                "artifact producing_commit_hash must be null or a lowercase SHA-256 hex digest"
                    .to_owned(),
            );
        }
    }
    if let Some(value) = object.get("metadata") {
        if !value.is_object() {
            return Err("artifact metadata must be an object".to_owned());
        }
    }
    if !matches!(
        object.get("type").and_then(Value::as_str),
        Some(
            "candidate"
                | "score"
                | "report"
                | "literature"
                | "lean"
                | "experiment"
                | "log"
                | "latex"
        )
    ) {
        return Err("artifact type is not supported".to_owned());
    }
    Ok(())
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn valid_action_types() -> &'static [&'static str] {
    const ACTION_TYPES: &[&str] = &[
        "InitRun",
        "AddCandidate",
        "WriteArtifact",
        "ValidateRun",
        "ControllerAction",
        "StageAStarted",
        "StageALLMCandidatesProposed",
        "Stage0OpportunityDiscovery",
        "Stage0Skipped",
        "StageADataGateDeferred",
        "StageACandidateGenerated",
        "StageAScoreComputed",
        "StageADuplicatePruned",
        "StageAGatePruned",
        "StageASurvivorsSelected",
        "StageAReportWritten",
        "QuestionerCheck",
        "RetrievalAdequacyDemo",
        "RetrievalRunRecorded",
        "StagnationDemo",
        "StageBStarted",
        "StageBChildGenerated",
        "StageBLLMReviewRecorded",
        "StageBReviewersRun",
        "StageBDisagreementResolved",
        "StageBBridgeChecked",
        "StageBBridgeRepaired",
        "StageBBaselineChecked",
        "StageBRedteamChecked",
        "StageBQuestionerRouted",
        "StageBScoreComputed",
        "StageBGatePruned",
        "StageBSurvivorsSelected",
        "StageBReportWritten",
        "StageCSelectionStarted",
        "StageCRedteamAggregated",
        "StageCUncertaintyComputed",
        "StageCScoreComputed",
        "StageCSelectionDecided",
        "StageCBudgetSelected",
        "StageCSelectionReportWritten",
        "StageCVerificationStarted",
        "StageCProofValidated",
        "StageCSyntheticExperimentRun",
        "StageCNoDataValidated",
        "StageCVerificationDecided",
        "StageCVerificationReportWritten",
        "AbstractSynthesisStarted",
        "AbstractModelProposed",
        "AbstractionAttackRun",
        "AbstractionReportWritten",
        "FinalNucleusSelected",
        "AbstractSynthesisReportWritten",
        "ManuscriptPlanningStarted",
        "ClaimTableBuilt",
        "BlockedClaimsIdentified",
        "ManuscriptPlanBuilt",
        "ManuscriptPlanReportWritten",
        "NarrativeContractWritten",
        "PaperShapeCritiqueWritten",
        "DraftSkeletonStarted",
        "DraftSkeletonBuilt",
        "ManuscriptChecklistBuilt",
        "DraftSkeletonReportWritten",
        "ManuscriptChecklistReportWritten",
        "ResearchObjectPackagingStarted",
        "ArtifactManifestWritten",
        "LedgerSummaryWritten",
        "BranchOutcomesWritten",
        "ReproducibilityManifestWritten",
        "ResearchObjectWritten",
        "PaperAssemblyStarted",
        "PaperSkeletonWritten",
        "PaperAssemblyReportWritten",
        "FinalAuditStarted",
        "FinalAuditReportWritten",
        "ReleaseGateDecided",
        "ExportPreparationStarted",
        "ProseGenerationContractWritten",
        "LatexExportPlanWritten",
        "ExportSectionMapWritten",
        "ExportClaimMapWritten",
        "ExportReadinessReportWritten",
        "ExportBundleManifestWritten",
        "ProseSectionDraftWritten",
        "ManuscriptDraftWritten",
        "CitationRegistryWritten",
        "LatexExportWritten",
        "PaperCriticReportWritten",
        "PaperRevisionWritten",
        "FullPaperGenerationWritten",
        "FullPaperReleaseEvaluated",
        "HumanReviewIngested",
        "ProofArtifactIngested",
        "ExperimentArtifactIngested",
        "ClaimEvidenceMapWritten",
        "EvidenceAwareRefreshWritten",
        "HumanReviewReconciliationWritten",
        "ReviewerChangeRequestsIngested",
        "HumanReviewReconciliationIndexWritten",
        "AutonomousEvidencePlanWritten",
        "AutonomousPlanExecutionWritten",
        "PlannedSpecExecutionWritten",
        "PythonExperimentSandboxWritten",
        "ExperimentGapRoutingWritten",
        "AutonomousLoopWritten",
        "GapAttemptHistoryWritten",
        "GapStrategyDiversificationWritten",
        "CapabilityEscalationWritten",
        "FinalManuscriptRegenerated",
        "FinalReleaseBundleAssembled",
        "AutonomousPaperRunWritten",
        "AutonomousPaperCheckpointWritten",
        "AutonomousPaperResumeWritten",
        "ScientificSubstrateBuilt",
        "SubstrateExperimentRouted",
        "SubstrateTournamentRun",
        "CreativeMutationPlanWritten",
        "CreativeMutationsApplied",
        "MutationTournamentRun",
        "CreativeSearchCycleWritten",
        "CreativeSearchControllerWritten",
        "GenerationMutationPlanWritten",
        "GenerationMutationsApplied",
        "OpportunityDiscoveryWritten",
        "VarianceAugmentationWritten",
        "VarianceAugmentationApplied",
        "VarianceSubstratesPromoted",
        "BranchRoutesWritten",
        "RouteExecutionSpecsWritten",
        "RouteExecutionRun",
        "ProductionModeChecked",
        "DomainMethodAtlasBuilt",
        "AtlasScanWritten",
        "DeepOpportunityDiscoveryWritten",
        "LLMVarianceGenerationWritten",
        "LLMIdeaTreeConstructionWritten",
        "LLMSubstrateConstructionWritten",
        "LLMRoutePlanningWritten",
        "LLMExperimentCodeWritten",
        "GeneratedExperimentExecutionWritten",
        "HybridEvidencePackagesPlanned",
        "HybridEvidencePackagesExecuted",
        "ScientificCriticReviewsWritten",
        "CrossPackageAdjudicationWritten",
        "NucleusManuscriptPlanned",
        "NucleusManuscriptSynthesized",
        "NucleusManuscriptRevised",
        "FinalPaperAssembled",
        "FinalPaperVerified",
        "FinalPaperRendered",
        "FinalPaperBundleBuilt",
        "LLMOrchestrationWritten",
        "PipelineRunReportWritten",
    ];
    ACTION_TYPES
}

fn is_valid_action_type(value: &str) -> bool {
    valid_action_types().contains(&value)
}

fn validate_kernel_response(response: &WireKernelResponse) -> Result<(), String> {
    if response.protocol_version.is_empty() {
        return Err("protocol_version must not be empty".to_owned());
    }
    if response.kernel_version.is_empty() {
        return Err("kernel_version must not be empty".to_owned());
    }
    if response.request_id.is_empty() {
        return Err("request_id must not be empty".to_owned());
    }
    let _ = response.mode;
    if response.operation == KernelOperation::ArtifactPersist {
        let persist_codes = [
            "artifact_persist_run_missing",
            "artifact_persist_directory_invalid",
            "artifact_persist_target_exists",
            "artifact_persist_payload_invalid",
            "artifact_persist_size_exceeded",
            "artifact_persist_temp_write_failed",
            "artifact_persist_publish_failed",
            "artifact_persist_temp_cleanup_warning",
            "artifact_persist_durability_uncertain",
            "artifact_persist_postcondition_failed",
        ];
        if response
            .diagnostics
            .iter()
            .any(|item| !persist_codes.contains(&item.code.as_str()))
        {
            return Err("artifact.persist response has an invalid diagnostic".to_owned());
        }
        if response.status == KernelResponseStatus::Accepted && !response.mutation_performed {
            return Err("accepted artifact.persist responses must report mutation".to_owned());
        }
        if response.status == KernelResponseStatus::Accepted
            && (response.diagnostics.len() > 1
                || response
                    .diagnostics
                    .iter()
                    .any(|item| item.code != "artifact_persist_temp_cleanup_warning"))
        {
            return Err("accepted artifact.persist diagnostics are invalid".to_owned());
        }
        if response.status != KernelResponseStatus::Accepted && response.mutation_performed {
            let allowed = [
                "artifact_persist_durability_uncertain",
                "artifact_persist_postcondition_failed",
            ];
            if response.status != KernelResponseStatus::Error
                || response.diagnostics.len() != 1
                || !allowed.contains(&response.diagnostics[0].code.as_str())
            {
                return Err(
                    "artifact.persist mutation flag is invalid for this response".to_owned(),
                );
            }
        }
        if response.status != KernelResponseStatus::Accepted && !response.mutation_performed {
            let disallowed = [
                "artifact_persist_temp_cleanup_warning",
                "artifact_persist_durability_uncertain",
                "artifact_persist_postcondition_failed",
            ];
            if response.diagnostics.len() != 1
                || disallowed.contains(&response.diagnostics[0].code.as_str())
            {
                return Err("artifact.persist pre-publication diagnostics are invalid".to_owned());
            }
        }
    } else if response.mutation_performed {
        return Err("kernel responses must not report mutations".to_owned());
    }
    for diagnostic in &response.diagnostics {
        if diagnostic.code.is_empty()
            || !diagnostic.code.bytes().enumerate().all(|(index, byte)| {
                byte.is_ascii_lowercase() || (index > 0 && (byte.is_ascii_digit() || byte == b'_'))
            })
        {
            return Err("diagnostic code does not match ^[a-z][a-z0-9_]*$".to_owned());
        }
        if diagnostic.message.is_empty() {
            return Err("diagnostic message must not be empty".to_owned());
        }
    }
    match &response.status {
        KernelResponseStatus::Accepted => validate_accepted_result(response),
        KernelResponseStatus::Rejected | KernelResponseStatus::Error => {
            if response.result.is_empty() {
                Ok(())
            } else {
                Err("rejected and error responses must have an empty result".to_owned())
            }
        }
    }
}

fn validate_accepted_result(response: &WireKernelResponse) -> Result<(), String> {
    match &response.operation {
        KernelOperation::HashCanonicalJson => {
            require_exact_keys(&response.result, &["canonical_json", "sha256"])?;
            if !response
                .result
                .get("canonical_json")
                .is_some_and(Value::is_string)
            {
                return Err("canonical_json must be a string".to_owned());
            }
            require_sha256_field(&response.result, "sha256")
        }
        KernelOperation::LedgerAppend => {
            require_exact_keys(
                &response.result,
                &[
                    "commit",
                    "previous_tip_hash",
                    "new_tip_hash",
                    "commit_count_before",
                    "commit_count_after",
                    "appended",
                    "linked_artifact_count",
                    "authority_granted",
                ],
            )?;
            for key in ["previous_tip_hash", "new_tip_hash"] {
                if response
                    .result
                    .get(key)
                    .and_then(Value::as_str)
                    .is_none_or(|v| !is_sha256_hex(v))
                {
                    return Err(format!("{key} must be a SHA-256 hash"));
                }
            }
            let before = response
                .result
                .get("commit_count_before")
                .and_then(Value::as_u64)
                .ok_or_else(|| "commit_count_before must be an integer".to_owned())?;
            let after = response
                .result
                .get("commit_count_after")
                .and_then(Value::as_u64)
                .ok_or_else(|| "commit_count_after must be an integer".to_owned())?;
            if before == 0
                || after != before + 1
                || response.result.get("appended") != Some(&Value::Bool(true))
                || response.result.get("linked_artifact_count") != Some(&Value::Number(0.into()))
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("ledger.append result flags are invalid".to_owned());
            }
            let commit = response
                .result
                .get("commit")
                .and_then(Value::as_object)
                .ok_or_else(|| "commit must be an object".to_owned())?;
            require_exact_keys(
                commit,
                &[
                    "commit_hash",
                    "parent_hash",
                    "run_id",
                    "candidate_id",
                    "action_type",
                    "payload",
                    "artifact_refs",
                    "timestamp",
                ],
            )?;
            if commit.get("commit_hash").and_then(Value::as_str)
                != response.result.get("new_tip_hash").and_then(Value::as_str)
                || commit.get("parent_hash").and_then(Value::as_str)
                    != response
                        .result
                        .get("previous_tip_hash")
                        .and_then(Value::as_str)
                || !commit.get("payload").is_some_and(Value::is_object)
                || commit.get("artifact_refs") != Some(&Value::Array(Vec::new()))
                || commit.get("authority_granted").is_some()
            {
                return Err("ledger.append commit fields are invalid".to_owned());
            }
            if commit
                .get("action_type")
                .and_then(Value::as_str)
                .is_none_or(|v| !is_valid_action_type(v) || v == "InitRun")
            {
                return Err("ledger.append action_type is invalid".to_owned());
            }
            let wire = serde_json::from_value::<WireLedgerCommit>(Value::Object(commit.clone()))
                .map_err(|error| format!("ledger.append commit is invalid: {error}"))?;
            validate_wire_commit_structure(&wire)?;
            if compute_wire_commit_hash(&wire).map_err(|error| error.to_owned())?
                != wire.commit_hash
            {
                return Err("ledger.append commit hash does not match its fields".to_owned());
            }
            Ok(())
        }
        KernelOperation::ArtifactPersist => {
            require_exact_keys(
                &response.result,
                &[
                    "artifact",
                    "bytes_written",
                    "created",
                    "linked_to_ledger",
                    "authority_granted",
                ],
            )?;
            let artifact = response
                .result
                .get("artifact")
                .and_then(Value::as_object)
                .ok_or_else(|| "artifact must be an object".to_owned())?;
            require_exact_keys(
                artifact,
                &[
                    "id",
                    "type",
                    "path",
                    "content_hash",
                    "producing_commit_hash",
                    "metadata",
                ],
            )?;
            for field in ["id", "type", "path"] {
                if artifact
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
                {
                    return Err(format!("artifact {field} must be a non-empty string"));
                }
            }
            require_sha256_field(artifact, "content_hash")?;
            if artifact.get("producing_commit_hash") != Some(&Value::Null) {
                return Err("artifact producing_commit_hash must be null".to_owned());
            }
            let wire_artifact =
                serde_json::from_value::<WireArtifactRef>(Value::Object(artifact.clone()))
                    .map_err(|error| format!("artifact is invalid: {error}"))?;
            let path_parts: Vec<&str> = wire_artifact.path.split('/').collect();
            if path_parts.len() != 4
                || !wire_artifact.path.ends_with(".json")
                || artifact_directory(&wire_artifact.artifact_type) != path_parts.get(2).copied()
                || validate_artifact_location(
                    path_parts.get(1).copied().unwrap_or(""),
                    &wire_artifact,
                )
                .is_err()
            {
                return Err("persisted artifact path does not match its type directory".to_owned());
            }
            if wire_artifact.metadata.len() > 66
                || wire_artifact.metadata.get("format") != Some(&Value::String("json".to_owned()))
                || wire_artifact.metadata.get("is_verification_evidence")
                    != Some(&Value::Bool(false))
                || wire_artifact.metadata.contains_key("producer")
                || scan_forbidden_authority_values(&Value::Object(wire_artifact.metadata.clone()))
            {
                return Err("persisted artifact metadata is invalid".to_owned());
            }
            if response
                .result
                .get("bytes_written")
                .and_then(Value::as_u64)
                .is_none_or(|n| n == 0)
            {
                return Err("bytes_written must be a positive integer".to_owned());
            }
            if response.result.get("created") != Some(&Value::Bool(true))
                || response.result.get("linked_to_ledger") != Some(&Value::Bool(false))
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("artifact.persist result has invalid flags".to_owned());
            }
            Ok(())
        }
        KernelOperation::ArtifactLink => {
            require_exact_keys(
                &response.result,
                &[
                    "artifact",
                    "sidecar_path",
                    "sidecar_content_hash",
                    "bytes_written",
                    "created",
                    "linked_to_ledger",
                    "authority_granted",
                ],
            )?;
            let artifact = response
                .result
                .get("artifact")
                .and_then(Value::as_object)
                .ok_or_else(|| "artifact must be an object".to_owned())?;
            require_exact_keys(
                artifact,
                &[
                    "id",
                    "type",
                    "path",
                    "content_hash",
                    "producing_commit_hash",
                    "metadata",
                ],
            )?;
            let wire_artifact =
                serde_json::from_value::<WireArtifactRef>(Value::Object(artifact.clone()))
                    .map_err(|error| format!("artifact is invalid: {error}"))?;
            if wire_artifact.producing_commit_hash.is_none() {
                return Err("linked artifact must have a producing commit".to_owned());
            }
            validate_artifact_location(
                wire_artifact.path.split('/').nth(1).unwrap_or_default(),
                &wire_artifact,
            )?;
            let sidecar_path = response
                .result
                .get("sidecar_path")
                .and_then(Value::as_str)
                .ok_or_else(|| "sidecar_path must be a string".to_owned())?;
            if sidecar_path != format!("{}.meta.json", wire_artifact.path) {
                return Err("sidecar_path does not match artifact path".to_owned());
            }
            require_sha256_field(&response.result, "sidecar_content_hash")?;
            if response
                .result
                .get("bytes_written")
                .and_then(Value::as_u64)
                .is_none_or(|n| n == 0)
                || response.result.get("created") != Some(&Value::Bool(true))
                || response.result.get("linked_to_ledger") != Some(&Value::Bool(true))
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("artifact.link result flags are invalid".to_owned());
            }
            Ok(())
        }
        KernelOperation::PersistenceCommitBundle => {
            require_exact_keys(
                &response.result,
                &[
                    "artifacts",
                    "commit",
                    "previous_tip_hash",
                    "new_tip_hash",
                    "commit_count_before",
                    "commit_count_after",
                    "artifact_count",
                    "sidecar_count",
                    "bundle_committed",
                    "recovered_from_intent",
                    "authority_granted",
                ],
            )?;
            for field in ["previous_tip_hash", "new_tip_hash"] {
                require_sha256_field(&response.result, field)?;
            }
            let before = response
                .result
                .get("commit_count_before")
                .and_then(Value::as_u64)
                .ok_or_else(|| "bundle commit_count_before must be an integer".to_owned())?;
            let after = response
                .result
                .get("commit_count_after")
                .and_then(Value::as_u64)
                .ok_or_else(|| "bundle commit_count_after must be an integer".to_owned())?;
            let artifact_count = response
                .result
                .get("artifact_count")
                .and_then(Value::as_u64)
                .ok_or_else(|| "bundle artifact_count must be an integer".to_owned())?;
            if before == 0
                || after != before + 1
                || !(1..=16).contains(&artifact_count)
                || response.result.get("sidecar_count")
                    != Some(&Value::Number(artifact_count.into()))
                || response.result.get("bundle_committed") != Some(&Value::Bool(true))
                || !response
                    .result
                    .get("recovered_from_intent")
                    .is_some_and(Value::is_boolean)
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("bundle result flags or counts are invalid".to_owned());
            }
            let artifacts = response
                .result
                .get("artifacts")
                .and_then(Value::as_array)
                .ok_or_else(|| "bundle artifacts must be an array".to_owned())?;
            if artifacts.len() != artifact_count as usize {
                return Err("bundle artifact count does not match artifacts".to_owned());
            }
            for artifact in artifacts {
                let object = artifact
                    .as_object()
                    .ok_or_else(|| "bundle artifact must be an object".to_owned())?;
                let wire = serde_json::from_value::<WireArtifactRef>(artifact.clone())
                    .map_err(|error| format!("bundle artifact is invalid: {error}"))?;
                if object.get("producing_commit_hash") != response.result.get("new_tip_hash")
                    || validate_artifact_location(
                        wire.path.split('/').nth(1).unwrap_or_default(),
                        &wire,
                    )
                    .is_err()
                    || wire.metadata.get("format") != Some(&Value::String("json".to_owned()))
                    || wire.metadata.get("is_verification_evidence") != Some(&Value::Bool(false))
                {
                    return Err("bundle artifact link or metadata is invalid".to_owned());
                }
            }
            let commit = response
                .result
                .get("commit")
                .and_then(Value::as_object)
                .ok_or_else(|| "bundle commit must be an object".to_owned())?;
            let wire = serde_json::from_value::<WireLedgerCommit>(Value::Object(commit.clone()))
                .map_err(|error| format!("bundle commit is invalid: {error}"))?;
            validate_wire_commit_structure(&wire)?;
            if wire.commit_hash
                != response
                    .result
                    .get("new_tip_hash")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                || wire.parent_hash.as_deref()
                    != response
                        .result
                        .get("previous_tip_hash")
                        .and_then(Value::as_str)
                || wire.artifact_refs.len() != artifacts.len()
                || compute_wire_commit_hash(&wire)? != wire.commit_hash
                || commit.get("artifact_refs") != Some(&Value::Array(artifacts.clone()))
            {
                return Err("bundle commit fields are invalid".to_owned());
            }
            Ok(())
        }
        KernelOperation::ProtocolValidate => {
            require_exact_keys(&response.result, &["valid", "protocol_name"])?;
            if response.result.get("valid") != Some(&Value::Bool(true)) {
                return Err("protocol validation result must be valid=true".to_owned());
            }
            let protocol_name = response
                .result
                .get("protocol_name")
                .and_then(Value::as_str)
                .ok_or_else(|| "protocol_name must be a string".to_owned())?;
            if !matches!(
                protocol_name,
                "KernelRequestEnvelope" | "KernelResponseEnvelope"
            ) {
                return Err("protocol_name is not supported".to_owned());
            }
            Ok(())
        }
        KernelOperation::ReplayVerifyCore => {
            require_exact_keys(
                &response.result,
                &[
                    "run_id",
                    "ledger_tip_hash",
                    "ledger_commit_count",
                    "ledger_artifact_count",
                    "ledger_artifact_inventory_hash",
                    "required_outputs_checked",
                    "manifest_artifact_id",
                    "manifest_producing_commit_hash",
                    "manifest_entry_count",
                    "manifest_inventory_hash",
                    "claims_checked",
                    "claim_evidence_links_checked",
                    "core_replay_valid",
                    "ledger_snapshot_stable",
                    "authority_boundary_valid",
                    "authority_granted",
                ],
            )?;
            for field in ["run_id", "manifest_artifact_id"] {
                if response
                    .result
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(|value| !is_safe_segment(value))
                {
                    return Err(format!("replay result {field} must be a safe identifier"));
                }
            }
            if response
                .result
                .get("manifest_artifact_id")
                .and_then(Value::as_str)
                != Some("artifact-manifest")
            {
                return Err("replay manifest_artifact_id is invalid".to_owned());
            }
            for field in [
                "ledger_tip_hash",
                "ledger_artifact_inventory_hash",
                "manifest_producing_commit_hash",
                "manifest_inventory_hash",
            ] {
                require_sha256_field(&response.result, field)?;
            }
            if response
                .result
                .get("required_outputs_checked")
                .and_then(Value::as_u64)
                != Some(REPLAY_REQUIRED_OUTPUTS.len() as u64)
            {
                return Err("replay required_outputs_checked must equal 11".to_owned());
            }
            for field in [
                "ledger_commit_count",
                "ledger_artifact_count",
                "required_outputs_checked",
                "manifest_entry_count",
                "claims_checked",
                "claim_evidence_links_checked",
            ] {
                if !response.result.get(field).is_some_and(Value::is_u64) {
                    return Err(format!("replay result {field} must be an integer"));
                }
            }
            for field in [
                "ledger_commit_count",
                "required_outputs_checked",
                "manifest_entry_count",
            ] {
                if response.result.get(field).and_then(Value::as_u64) == Some(0) {
                    return Err(format!("replay result {field} must be positive"));
                }
            }
            if response.result.get("core_replay_valid") != Some(&Value::Bool(true))
                || response.result.get("ledger_snapshot_stable") != Some(&Value::Bool(true))
                || response.result.get("authority_boundary_valid") != Some(&Value::Bool(true))
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("replay result has invalid authority flags".to_owned());
            }
            Ok(())
        }
        KernelOperation::ArtifactVerify => {
            require_allowed_keys(
                &response.result,
                &[
                    "valid",
                    "run_id",
                    "artifact_id",
                    "content_hash",
                    "producing_commit_hash",
                ],
            )?;
            if response.result.get("valid") != Some(&Value::Bool(true)) {
                return Err("artifact verification result must be valid=true".to_owned());
            }
            for field in ["run_id", "artifact_id"] {
                if response
                    .result
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
                {
                    return Err(format!(
                        "artifact verification {field} must be a non-empty string"
                    ));
                }
            }
            require_sha256_field(&response.result, "content_hash")?;
            if let Some(value) = response.result.get("producing_commit_hash") {
                if !value.is_null() && !value.as_str().is_some_and(is_sha256_hex) {
                    return Err("producing_commit_hash must be null or a SHA-256 digest".to_owned());
                }
            }
            Ok(())
        }
        KernelOperation::LedgerVerify => {
            require_allowed_keys(
                &response.result,
                &["valid", "run_id", "commit_count", "root_hash", "tip_hash"],
            )?;
            if response.result.get("valid") != Some(&Value::Bool(true)) {
                return Err("ledger verification result must be valid=true".to_owned());
            }
            if response
                .result
                .get("run_id")
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
            {
                return Err("ledger verification run_id must be a non-empty string".to_owned());
            }
            if !response
                .result
                .get("commit_count")
                .is_some_and(Value::is_u64)
            {
                return Err("ledger verification commit_count must be an integer".to_owned());
            }
            for field in ["root_hash", "tip_hash"] {
                if let Some(value) = response.result.get(field) {
                    if !value.is_null() && !value.as_str().is_some_and(is_sha256_hex) {
                        return Err(format!("{field} must be null or a SHA-256 digest"));
                    }
                }
            }
            Ok(())
        }
        KernelOperation::EvidenceClassify => {
            require_allowed_keys(
                &response.result,
                &[
                    "run_id",
                    "artifact_id",
                    "authority_class",
                    "candidate_kind",
                    "compatibility_only",
                    "authority_granted",
                ],
            )?;
            for field in [
                "run_id",
                "artifact_id",
                "authority_class",
                "compatibility_only",
                "authority_granted",
            ] {
                if !response.result.contains_key(field) {
                    return Err(format!(
                        "evidence classification result is missing field: {field}"
                    ));
                }
            }
            for field in ["run_id", "artifact_id"] {
                if response
                    .result
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
                {
                    return Err(format!(
                        "evidence classification {field} must be a non-empty string"
                    ));
                }
            }
            let authority_class = response
                .result
                .get("authority_class")
                .and_then(Value::as_str)
                .ok_or_else(|| "authority_class must be a string".to_owned())?;
            if !matches!(
                authority_class,
                "Context" | "Presentation" | "CapabilityCandidate"
            ) {
                return Err("authority_class is not supported".to_owned());
            }
            if let Some(value) = response.result.get("candidate_kind") {
                if !value.is_null()
                    && !matches!(value.as_str(), Some("LeanProof" | "SyntheticExperiment"))
                {
                    return Err("candidate_kind is not supported".to_owned());
                }
            }
            let is_candidate = authority_class == "CapabilityCandidate";
            let has_candidate_kind = response
                .result
                .get("candidate_kind")
                .is_some_and(|value| !value.is_null());
            if is_candidate != has_candidate_kind {
                return Err(
                    "CapabilityCandidate requires candidate_kind and other classes forbid it"
                        .to_owned(),
                );
            }
            if !response
                .result
                .get("compatibility_only")
                .is_some_and(Value::is_boolean)
            {
                return Err("compatibility_only must be a boolean".to_owned());
            }
            if response
                .result
                .get("compatibility_only")
                .and_then(Value::as_bool)
                == Some(true)
                && !is_candidate
            {
                return Err("compatibility_only is valid only for capability candidates".to_owned());
            }
            if response.result.get("authority_granted") != Some(&Value::Bool(false)) {
                return Err("evidence classification cannot grant authority".to_owned());
            }
            Ok(())
        }
        KernelOperation::EvidenceValidateBundle => {
            require_exact_keys(
                &response.result,
                &[
                    "run_id",
                    "candidate_id",
                    "claim_id",
                    "bundle_kind",
                    "producing_commit_hash",
                    "validated_artifact_ids",
                    "bundle_valid",
                    "authority_granted",
                ],
            )?;
            for field in ["run_id", "candidate_id", "claim_id", "bundle_kind"] {
                if response
                    .result
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
                {
                    return Err(format!("bundle result {field} must be a non-empty string"));
                }
            }
            if !matches!(
                response.result.get("bundle_kind").and_then(Value::as_str),
                Some("LeanProof" | "SyntheticExperiment")
            ) {
                return Err("bundle_kind is not supported".to_owned());
            }
            require_sha256_field(&response.result, "producing_commit_hash")?;
            if response.result.get("bundle_valid") != Some(&Value::Bool(true))
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("bundle validation result has invalid authority flags".to_owned());
            }
            let ids = response
                .result
                .get("validated_artifact_ids")
                .and_then(Value::as_array)
                .ok_or_else(|| "validated_artifact_ids must be an array".to_owned())?;
            let expected_count = if response.result.get("bundle_kind").and_then(Value::as_str)
                == Some("LeanProof")
            {
                5
            } else {
                6
            };
            if ids.len() != expected_count
                || ids
                    .iter()
                    .any(|id| id.as_str().is_none_or(|value| !is_safe_segment(value)))
                || ids
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<HashSet<_>>()
                    .len()
                    != expected_count
            {
                return Err("validated_artifact_ids are invalid".to_owned());
            }
            Ok(())
        }
        KernelOperation::ClaimResolve => {
            require_exact_keys(
                &response.result,
                &[
                    "run_id",
                    "candidate_id",
                    "claim_id",
                    "claim_text_hash",
                    "claim_label",
                    "allowed_in_main_text",
                    "allowed_section",
                    "claim_record_validated",
                    "admissible",
                    "evidence_bundle_validated",
                    "authority_granted",
                ],
            )?;
            for field in [
                "run_id",
                "candidate_id",
                "claim_id",
                "claim_label",
                "allowed_section",
            ] {
                if response
                    .result
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
                {
                    return Err(format!(
                        "claim resolution {field} must be a non-empty string"
                    ));
                }
            }
            require_sha256_field(&response.result, "claim_text_hash")?;
            if !matches!(
                response.result.get("claim_label").and_then(Value::as_str),
                Some(
                    "LeanVerified"
                        | "ExperimentVerified"
                        | "SyntheticExperimentVerified"
                        | "RealDataExperimentVerified"
                        | "Conjecture"
                        | "NegativeResult"
                        | "Limitation"
                        | "Unsupported",
                )
            ) {
                return Err("claim_label is not supported".to_owned());
            }
            for field in [
                "allowed_in_main_text",
                "admissible",
                "evidence_bundle_validated",
            ] {
                if !response.result.get(field).is_some_and(Value::is_boolean) {
                    return Err(format!("claim resolution {field} must be a boolean"));
                }
            }
            if response.result.get("claim_record_validated") != Some(&Value::Bool(true)) {
                return Err("claim resolution requires a validated claim record".to_owned());
            }
            if response.result.get("authority_granted") != Some(&Value::Bool(false)) {
                return Err("claim resolution cannot grant authority".to_owned());
            }
            Ok(())
        }
        KernelOperation::CheckpointVerify => {
            require_exact_keys(
                &response.result,
                &[
                    "run_id",
                    "checkpoint_index_artifact_id",
                    "checkpoint_index_producing_commit_hash",
                    "checkpoint_count",
                    "validated_checkpoint_hashes",
                    "latest_checkpoint_hash",
                    "latest_completed_stage",
                    "validated_output_count",
                    "checkpoint_chain_valid",
                    "resume_allowed",
                    "authority_granted",
                ],
            )?;
            for field in [
                "run_id",
                "checkpoint_index_artifact_id",
                "latest_completed_stage",
            ] {
                if response
                    .result
                    .get(field)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
                {
                    return Err(format!(
                        "checkpoint result {field} must be a non-empty string"
                    ));
                }
            }
            require_sha256_field(&response.result, "checkpoint_index_producing_commit_hash")?;
            require_sha256_field(&response.result, "latest_checkpoint_hash")?;
            for field in ["checkpoint_count", "validated_output_count"] {
                if !response.result.get(field).is_some_and(Value::is_u64) {
                    return Err(format!("checkpoint result {field} must be an integer"));
                }
            }
            let count = response
                .result
                .get("checkpoint_count")
                .and_then(Value::as_u64)
                .ok_or_else(|| "checkpoint_count must be an integer".to_owned())?;
            let hashes = response
                .result
                .get("validated_checkpoint_hashes")
                .and_then(Value::as_array)
                .ok_or_else(|| "validated_checkpoint_hashes must be an array".to_owned())?;
            if count == 0
                || hashes.len() != count as usize
                || hashes
                    .iter()
                    .any(|hash| hash.as_str().is_none_or(|value| !is_sha256_hex(value)))
                || hashes
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<HashSet<_>>()
                    .len()
                    != hashes.len()
                || hashes.last().and_then(Value::as_str)
                    != response
                        .result
                        .get("latest_checkpoint_hash")
                        .and_then(Value::as_str)
            {
                return Err("validated checkpoint hashes are invalid".to_owned());
            }
            if response.result.get("checkpoint_chain_valid") != Some(&Value::Bool(true))
                || !response
                    .result
                    .get("resume_allowed")
                    .is_some_and(Value::is_boolean)
                || response.result.get("authority_granted") != Some(&Value::Bool(false))
            {
                return Err("checkpoint verification result has invalid authority flags".to_owned());
            }
            Ok(())
        }
    }
}

fn require_exact_keys(result: &Map<String, Value>, keys: &[&str]) -> Result<(), String> {
    require_allowed_keys(result, keys)?;
    if result.len() != keys.len() || keys.iter().any(|key| !result.contains_key(*key)) {
        return Err("response result has missing or extra fields".to_owned());
    }
    Ok(())
}

fn require_allowed_keys(result: &Map<String, Value>, keys: &[&str]) -> Result<(), String> {
    if result.keys().any(|key| !keys.contains(&key.as_str())) {
        return Err("response result has an unknown field".to_owned());
    }
    Ok(())
}

fn require_sha256_field(result: &Map<String, Value>, field: &str) -> Result<(), String> {
    if result
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(is_sha256_hex)
    {
        Ok(())
    } else {
        Err(format!("{field} must be a lowercase SHA-256 hex digest"))
    }
}

fn parse_json_without_duplicate_keys(input: &str) -> Result<Value, KernelError> {
    let mut deserializer = serde_json::Deserializer::from_str(input);
    let value = JsonValueSeed
        .deserialize(&mut deserializer)
        .map_err(|error| KernelError::InvalidRequest(error.to_string()))?;
    deserializer
        .end()
        .map_err(|error| KernelError::InvalidRequest(error.to_string()))?;
    Ok(value)
}

struct JsonValueSeed;

impl<'de> DeserializeSeed<'de> for JsonValueSeed {
    type Value = Value;

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(JsonValueVisitor)
    }
}

struct JsonValueVisitor;

impl<'de> Visitor<'de> for JsonValueVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a JSON value without duplicate object keys")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(Value::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::Number(value.into()))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::Number(value.into()))
    }

    fn visit_i128<E>(self, value: i128) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        serde_json::Number::from_i128(value)
            .map(Value::Number)
            .ok_or_else(|| de::Error::custom("integer exceeds JSON number range"))
    }

    fn visit_u128<E>(self, value: u128) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        serde_json::Number::from_u128(value)
            .map(Value::Number)
            .ok_or_else(|| de::Error::custom("integer exceeds JSON number range"))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        serde_json::Number::from_f64(value)
            .map(Value::Number)
            .ok_or_else(|| de::Error::custom("non-finite JSON number"))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(Value::String(value))
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_newtype_struct<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(self)
    }

    fn visit_seq<A>(self, mut access: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = access.next_element_seed(JsonValueSeed)? {
            values.push(value);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A>(self, mut access: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut values = Map::new();
        while let Some(key) = access.next_key::<String>()? {
            if key == "$serde_json::private::Number" {
                if !values.is_empty() {
                    return Err(de::Error::custom(
                        "arbitrary-precision number wrapper must be a single map entry",
                    ));
                }
                let raw = access.next_value::<String>()?;
                let number = raw.parse::<serde_json::Number>().map_err(|error| {
                    de::Error::custom(format!("invalid arbitrary-precision number: {error}"))
                })?;
                if access.next_key::<String>()?.is_some() {
                    return Err(de::Error::custom(
                        "arbitrary-precision number wrapper has extra fields",
                    ));
                }
                return Ok(Value::Number(number));
            }
            if values.contains_key(&key) {
                return Err(de::Error::custom(format!("duplicate object key: {key}")));
            }
            let value = access.next_value_seed(JsonValueSeed)?;
            values.insert(key, value);
        }
        Ok(Value::Object(values))
    }
}

fn map_value<const N: usize>(entries: [(&str, Value); N]) -> Map<String, Value> {
    entries
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}

fn accepted_response(
    request_id: String,
    operation: KernelOperation,
    mode: KernelMode,
    result: Map<String, Value>,
) -> KernelResponse {
    accepted_response_with_diagnostics(request_id, operation, mode, result, Vec::new())
}

fn accepted_response_with_diagnostics(
    request_id: String,
    operation: KernelOperation,
    mode: KernelMode,
    result: Map<String, Value>,
    diagnostics: Vec<KernelDiagnostic>,
) -> KernelResponse {
    KernelResponse {
        protocol_version: PROTOCOL_VERSION,
        kernel_version: KERNEL_VERSION,
        request_id,
        operation,
        mode,
        status: KernelResponseStatus::Accepted,
        result,
        diagnostics,
        mutation_performed: false,
    }
}

fn rejected_or_error_response(
    request_id: String,
    operation: KernelOperation,
    mode: KernelMode,
    status: KernelResponseStatus,
    code: &str,
    message: String,
    path: Option<&str>,
) -> KernelResponse {
    KernelResponse {
        protocol_version: PROTOCOL_VERSION,
        kernel_version: KERNEL_VERSION,
        request_id,
        operation,
        mode,
        status,
        result: Map::new(),
        diagnostics: vec![KernelDiagnostic {
            code: code.to_owned(),
            message,
            path: path.map(str::to_owned),
        }],
        mutation_performed: false,
    }
}

#[allow(clippy::too_many_arguments)]
fn rejected_or_error_response_with_mutation(
    request_id: String,
    operation: KernelOperation,
    mode: KernelMode,
    status: KernelResponseStatus,
    code: &str,
    message: String,
    path: Option<&str>,
    mutation_performed: bool,
) -> KernelResponse {
    KernelResponse {
        protocol_version: PROTOCOL_VERSION,
        kernel_version: KERNEL_VERSION,
        request_id,
        operation,
        mode,
        status,
        result: Map::new(),
        diagnostics: vec![KernelDiagnostic {
            code: code.to_owned(),
            message,
            path: path.map(str::to_owned),
        }],
        mutation_performed,
    }
}

fn rejected_response(
    request_id: String,
    operation: KernelOperation,
    mode: KernelMode,
    code: &str,
    message: String,
    path: Option<&str>,
) -> KernelResponse {
    KernelResponse {
        protocol_version: PROTOCOL_VERSION,
        kernel_version: KERNEL_VERSION,
        request_id,
        operation,
        mode,
        status: KernelResponseStatus::Rejected,
        result: Map::new(),
        diagnostics: vec![KernelDiagnostic {
            code: code.to_owned(),
            message,
            path: path.map(str::to_owned),
        }],
        mutation_performed: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_json_sorts_objects_recursively() {
        let value: Value = serde_json::from_str(r#"{"z":[3,{"b":true,"a":null}],"a":"café"}"#)
            .expect("valid JSON");
        assert_eq!(
            canonical_json(&value).expect("canonical JSON"),
            r#"{"a":"café","z":[3,{"a":null,"b":true}]}"#
        );
    }

    #[test]
    fn canonical_json_matches_checked_in_golden_corpus() {
        #[derive(serde::Deserialize)]
        struct GoldenCase {
            value: Value,
            canonical_json: String,
            sha256: String,
        }

        let cases: Vec<GoldenCase> =
            serde_json::from_str(include_str!("../fixtures/canonical-json.json"))
                .expect("valid canonical JSON fixture");
        for case in cases {
            let rendered = canonical_json(&case.value).expect("canonical JSON");
            assert_eq!(rendered, case.canonical_json);
            assert_eq!(sha256_hex(rendered.as_bytes()), case.sha256);
        }
    }

    #[test]
    fn commit_hash_payload_matches_checked_in_golden_corpus() {
        #[derive(serde::Deserialize)]
        struct GoldenCase {
            value: Value,
            canonical_json: String,
            sha256: String,
        }

        let cases: Vec<GoldenCase> =
            serde_json::from_str(include_str!("../fixtures/ledger-commit-hashes.json"))
                .expect("valid ledger hash fixture");
        for case in cases {
            let rendered = canonical_json(&case.value).expect("canonical JSON");
            assert_eq!(rendered, case.canonical_json);
            assert_eq!(sha256_hex(rendered.as_bytes()), case.sha256);
        }
    }

    #[test]
    fn hash_operation_is_read_only_and_returns_digest() {
        let request: KernelRequest = serde_json::from_str(
            r#"{"protocol_version":"0.90.0","request_id":"r1","operation":"hash.canonical_json","mode":"DevelopmentCompatibility","payload":{"value":{"b":2,"a":1}}}"#,
        )
        .expect("valid request");
        let response = handle_request(request, None);
        assert!(!response.mutation_performed);
        assert_eq!(
            response.result.get("sha256").and_then(Value::as_str),
            Some("43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777")
        );
    }

    #[test]
    fn wrong_protocol_version_is_rejected_without_mutation() {
        let request: KernelRequest = serde_json::from_str(
            r#"{"protocol_version":"0.79.0","request_id":"r1","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{"protocol_name":"KernelRequestEnvelope","instance":{}}}"#,
        )
        .expect("valid request");
        let response = handle_request(request, None);
        assert_eq!(response.status, KernelResponseStatus::Rejected);
        assert_eq!(response.diagnostics[0].code, "protocol_version_mismatch");
        assert!(!response.mutation_performed);
    }

    #[test]
    fn protocol_validation_rejects_unknown_or_malformed_instances() {
        let request: KernelRequest = serde_json::from_str(
            r#"{"protocol_version":"0.90.0","request_id":"r1","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{"protocol_name":"KernelRequestEnvelope","instance":{"protocol_version":"0.90.0","request_id":"r2","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{},"unexpected":true}}}"#,
        )
        .expect("valid request");
        let response = handle_request(request, None);
        assert_eq!(response.status, KernelResponseStatus::Rejected);
        assert_eq!(response.diagnostics[0].code, "protocol_invalid");
    }

    #[test]
    fn malformed_transport_input_still_returns_one_error_envelope() {
        let response = transport_error_response(&KernelError::InvalidJson("bad".to_owned()));
        assert_eq!(response.status, KernelResponseStatus::Error);
        assert_eq!(response.request_id, "transport-error");
        assert_eq!(response.diagnostics[0].code, "transport_invalid");
    }

    #[test]
    fn duplicate_object_keys_are_rejected_recursively() {
        let error = parse_and_handle(
            r#"{"protocol_version":"0.89.0","request_id":"r1","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{"value":{"a":1,"a":2}}}"#,
        )
        .expect_err("duplicate keys must be rejected");
        assert!(error.to_string().contains("duplicate object key"));
    }

    #[test]
    fn ledger_action_types_are_closed_to_the_python_protocol_enum() {
        let schema: Value = serde_json::from_str(include_str!(
            "../../protocols/jsonschema/kernel-request-envelope.schema.json"
        ))
        .expect("valid generated kernel request schema");
        let schema_values = schema
            .pointer("/$defs/ControllerActionType/enum")
            .and_then(Value::as_array)
            .expect("ControllerActionType enum in generated schema");
        let schema_types: HashSet<&str> = schema_values
            .iter()
            .map(|value| value.as_str().expect("string action type"))
            .collect();
        let rust_types: HashSet<&str> = valid_action_types().iter().copied().collect();

        assert_eq!(rust_types, schema_types);
        assert!(!is_valid_action_type("UnknownAction"));
        assert!(!is_valid_action_type("init_run"));
    }

    #[test]
    fn strict_bundle_safety_helpers_match_the_frozen_contract() {
        assert!(contains_forbidden_proof_token(
            "claim",
            "theorem body uses sorry",
            &[],
        ));
        assert!(!contains_forbidden_proof_token(
            "claim",
            "theorem body is complete",
            &[],
        ));
        assert!(!allowed_imports_are_safe(&["/tmp/unsafe".to_owned()]));
        assert!(!allowed_imports_are_safe(&[
            "http://example.invalid".to_owned()
        ]));
        assert!(allowed_imports_are_safe(&[
            "Mathlib.Data.Nat.Basic".to_owned()
        ]));
        assert!(SUPPORTED_SYNTHETIC_EXPERIMENT_KINDS.contains(&"SyntheticSimulation"));
        assert!(!SUPPORTED_SYNTHETIC_EXPERIMENT_KINDS.contains(&"InventedExperiment"));
        assert!(contains_required_forbidden_inputs(&[
            "PublicDownload".to_owned(),
            "UserProvided".to_owned(),
            "RealWorldData".to_owned(),
            "network".to_owned(),
            "absolute_path".to_owned(),
        ]));
        assert!(!contains_required_forbidden_inputs(&[
            "PublicDownload".to_owned(),
            "UserProvided".to_owned(),
            "RealWorldData".to_owned(),
        ]));
    }

    #[test]
    fn strict_bundle_external_input_scan_covers_keys_values_and_forbidden_inputs() {
        assert!(contains_network_or_absolute_marker(&serde_json::json!({
            "https://example.invalid": 1
        })));
        assert!(contains_network_or_absolute_marker(&serde_json::json!({
            "source": "load /tmp/data"
        })));
        assert!(contains_network_or_absolute_marker(&serde_json::json!([
            "https://example.invalid/data"
        ])));
        assert!(contains_network_or_absolute_marker(&serde_json::json!({
            "source": "~/private/data"
        })));
        assert!(!contains_network_or_absolute_marker(&serde_json::json!({
            "source": "generated in memory"
        })));
    }

    #[test]
    fn strict_bundle_acceptance_rejects_malformed_or_inverted_bounds() {
        let metrics = serde_json::json!({"score": 0.5});
        let metrics = metrics.as_object().expect("object metrics");

        assert!(acceptance_satisfied(
            metrics,
            serde_json::json!({"score": {"min": 0.0, "max": 1.0}})
                .as_object()
                .expect("object criteria")
        ));
        assert!(!acceptance_satisfied(
            metrics,
            serde_json::json!({"score": {"min": 0.0, "max": "invalid"}})
                .as_object()
                .expect("object criteria")
        ));
        assert!(!acceptance_satisfied(
            metrics,
            serde_json::json!({"score": {"min": 0.8, "max": 0.2}})
                .as_object()
                .expect("object criteria")
        ));
        assert!(!acceptance_satisfied(
            metrics,
            serde_json::json!({"score": {"min": true}})
                .as_object()
                .expect("object criteria")
        ));
    }

    #[test]
    fn strict_bundle_lean_module_names_require_valid_segments() {
        assert!(allowed_imports_are_safe(&[
            "Mathlib.Data.Nat.Basic".to_owned(),
            "_Internal.Module2".to_owned(),
        ]));
        assert!(!allowed_imports_are_safe(&[".".to_owned()]));
        assert!(!allowed_imports_are_safe(&["Mathlib..Nat".to_owned()]));
        assert!(!allowed_imports_are_safe(&["2Mathlib.Nat".to_owned()]));
    }

    #[test]
    fn strict_bundle_structure_preserves_duplicate_member_diagnostic() {
        let request_value = serde_json::json!({
            "protocol_version": "0.90.0",
            "request_id": "duplicate-bundle-member",
            "operation": "evidence.validate_bundle",
            "mode": "StrictProduction",
            "payload": {
                "run_id": "run-001",
                "candidate_id": "candidate-001",
                "claim_id": "claim-001",
                "producing_commit_hash": "f".repeat(64),
                "bundle": {
                    "kind": "SyntheticExperiment",
                    "contract_artifact_id": "contract-001",
                    "input_artifact_id": "contract-001",
                    "trace_artifact_id": "trace-001",
                    "output_artifact_id": "output-001",
                    "result_artifact_id": "result-001",
                    "safety_artifact_id": "safety-001"
                }
            }
        });
        serde_json::from_value::<KernelRequest>(request_value.clone())
            .expect("valid typed request");

        let input = serde_json::to_string(&request_value).expect("serializable request");
        let response = parse_and_handle(&input).expect("operation-level rejection");
        assert_eq!(response.status, KernelResponseStatus::Rejected);
        assert_eq!(response.diagnostics[0].code, "bundle_member_duplicate");
    }

    #[test]
    fn strict_bundle_trace_schemas_reject_negative_elapsed_time() {
        let proof_trace = r#"{
            "backend":"lean","provider":"lean","tool_name":"lean","exit_code":0,
            "stdout":"","stderr":"","elapsed_ms":-1,"tool_version":null,
            "fake":false,"is_verification_evidence":true
        }"#;
        let synthetic_trace = r#"{
            "backend":"local_synthetic","provider":"local","runner_name":"runner",
            "exit_code":0,"stdout":"","stderr":"","elapsed_ms":-1,
            "runner_version":null,"fake":false,"is_verification_evidence":true
        }"#;

        assert!(serde_json::from_str::<ProofTraceWire>(proof_trace).is_err());
        assert!(serde_json::from_str::<SyntheticTraceWire>(synthetic_trace).is_err());
    }

    #[test]
    fn replay_authority_helpers_reject_assertions_without_name_false_positives() {
        assert!(scan_forbidden_authority_values(&serde_json::json!({
            "publication_ready": true
        })));
        assert!(!scan_forbidden_authority_values(&serde_json::json!({
            "publication_ready": false,
            "label": "LeanVerified"
        })));
        let derived_report = WireArtifactRef {
            id: "artifact-manifest".to_owned(),
            artifact_type: "report".to_owned(),
            path: "runs/run-1/research_object/artifact-manifest.json".to_owned(),
            content_hash: "0".repeat(64),
            producing_commit_hash: Some("1".repeat(64)),
            metadata: Map::new(),
        };
        let proof_with_domain_word = WireArtifactRef {
            id: "fake-proof-controlled-release-theorem".to_owned(),
            artifact_type: "lean".to_owned(),
            path: "runs/run-1/lean/fake-proof-controlled-release-theorem.json".to_owned(),
            content_hash: "0".repeat(64),
            producing_commit_hash: Some("1".repeat(64)),
            metadata: Map::new(),
        };
        assert!(is_forbidden_derived_evidence(&derived_report));
        assert!(!is_forbidden_derived_evidence(&proof_with_domain_word));
    }

    fn artifact_persist_test_root(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "factori-kernel-{label}-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("runs/run-1/candidates")).expect("test run directories");
        root
    }

    fn artifact_persist_test_payload() -> Map<String, Value> {
        serde_json::json!({
            "run_id": "run-1",
            "artifact_id": "artifact-1",
            "artifact_type": "candidate",
            "json_value": {"nested": [1, -0.0, "é"]},
            "metadata": {},
            "filename_stem_optional": null,
            "overwrite_policy": "FailIfExists"
        })
        .as_object()
        .expect("object payload")
        .clone()
    }

    #[test]
    fn artifact_persist_prepublication_faults_do_not_mutate() {
        let root = artifact_persist_test_root("prepublication-faults");
        let payload = artifact_persist_test_payload();
        for (fault, expected_code) in [
            (
                ArtifactPersistFault::TempCreate,
                "artifact_persist_temp_write_failed",
            ),
            (
                ArtifactPersistFault::TempWrite,
                "artifact_persist_temp_write_failed",
            ),
            (
                ArtifactPersistFault::TempFlush,
                "artifact_persist_temp_write_failed",
            ),
            (
                ArtifactPersistFault::TempFsync,
                "artifact_persist_temp_write_failed",
            ),
            (
                ArtifactPersistFault::Publish,
                "artifact_persist_publish_failed",
            ),
        ] {
            let error = persist_artifact_payload_with_fault(&payload, &root, fault)
                .expect_err("injected pre-publication fault");
            assert_eq!(error.status, KernelResponseStatus::Rejected);
            assert_eq!(error.code, expected_code);
            assert!(!error.mutation_performed);
            assert!(!root.join("runs/run-1/candidates/artifact-1.json").exists());
            assert_eq!(
                fs::read_dir(root.join("runs/run-1/candidates"))
                    .expect("artifact directory")
                    .count(),
                0
            );
        }
        fs::remove_dir_all(&root).expect("remove test root");
    }

    #[test]
    fn artifact_persist_cleanup_fault_returns_valid_warning() {
        let root = artifact_persist_test_root("cleanup-fault");
        let (result, diagnostics) = persist_artifact_payload_with_fault(
            &artifact_persist_test_payload(),
            &root,
            ArtifactPersistFault::TempCleanup,
        )
        .expect("cleanup failure is post-publication warning");
        assert_eq!(result.get("created"), Some(&Value::Bool(true)));
        assert_eq!(diagnostics.len(), 1);
        assert_eq!(diagnostics[0].code, "artifact_persist_temp_cleanup_warning");
        assert!(root.join("runs/run-1/candidates/artifact-1.json").is_file());
        fs::remove_dir_all(&root).expect("remove test root");
    }

    #[test]
    fn artifact_persist_postpublication_faults_report_mutation() {
        for (fault, expected_code) in [
            (
                ArtifactPersistFault::DirectoryFsync,
                "artifact_persist_durability_uncertain",
            ),
            (
                ArtifactPersistFault::Postcondition,
                "artifact_persist_postcondition_failed",
            ),
        ] {
            let root = artifact_persist_test_root(expected_code);
            let error =
                persist_artifact_payload_with_fault(&artifact_persist_test_payload(), &root, fault)
                    .expect_err("injected post-publication fault");
            assert_eq!(error.status, KernelResponseStatus::Error);
            assert_eq!(error.code, expected_code);
            assert!(error.mutation_performed);
            assert!(root.join("runs/run-1/candidates/artifact-1.json").is_file());
            fs::remove_dir_all(&root).expect("remove test root");
        }
    }

    fn ledger_append_test_root(label: &str) -> (PathBuf, String) {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "factori-kernel-ledger-{label}-{}-{nonce}",
            std::process::id()
        ));
        let run = root.join("runs/run-1");
        fs::create_dir_all(&run).expect("test run directory");
        let connection = Connection::open(run.join("ledger.sqlite")).expect("test ledger");
        connection
            .execute_batch(
                "CREATE TABLE commits (
                    commit_hash TEXT PRIMARY KEY,
                    parent_hash TEXT REFERENCES commits(commit_hash),
                    run_id TEXT NOT NULL,
                    candidate_id TEXT,
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                CREATE TRIGGER commits_no_update BEFORE UPDATE ON commits BEGIN
                    SELECT RAISE(ABORT, 'commits are append-only');
                END;
                CREATE TRIGGER commits_no_delete BEFORE DELETE ON commits BEGIN
                    SELECT RAISE(ABORT, 'commits are append-only');
                END;",
            )
            .expect("test ledger schema");
        let root_commit = WireLedgerCommit {
            commit_hash: String::new(),
            parent_hash: None,
            run_id: "run-1".to_owned(),
            candidate_id: None,
            action_type: "InitRun".to_owned(),
            payload: Map::new(),
            artifact_refs: Vec::new(),
            timestamp: "2026-01-01T00:00:00Z".to_owned(),
        };
        let root_hash = compute_wire_commit_hash(&root_commit).expect("root hash");
        connection
            .execute(
                "INSERT INTO commits VALUES (?1, NULL, 'run-1', NULL, 'InitRun', '{}', '[]', '2026-01-01T00:00:00Z')",
                [&root_hash],
            )
            .expect("root commit");
        drop(connection);
        (root, root_hash)
    }

    fn ledger_append_test_payload(root_hash: &str) -> Map<String, Value> {
        serde_json::json!({
            "run_id": "run-1",
            "expected_tip_hash": root_hash,
            "action_type": "AddCandidate",
            "payload": {"nested": [1, "é"], "empty": {}},
            "candidate_id_optional": "candidate-1",
            "timestamp": "2026-01-01T00:00:01.123456Z"
        })
        .as_object()
        .expect("object payload")
        .clone()
    }

    #[test]
    fn ledger_append_precommit_faults_preserve_exact_database_bytes() {
        for fault in [
            LedgerAppendFault::Open,
            LedgerAppendFault::Begin,
            LedgerAppendFault::Validate,
            LedgerAppendFault::Insert,
            LedgerAppendFault::Readback,
            LedgerAppendFault::Rollback,
        ] {
            let (root, root_hash) = ledger_append_test_root("precommit-fault");
            let path = root.join("runs/run-1/ledger.sqlite");
            let before = fs::read(&path).expect("ledger bytes");
            let error = append_ledger_payload_with_fault(
                &ledger_append_test_payload(&root_hash),
                &root,
                fault,
            )
            .expect_err("injected pre-commit fault");
            assert!(!error.mutation_performed);
            assert_eq!(fs::read(&path).expect("ledger bytes"), before);
            assert!(!root.join("runs/run-1/ledger.sqlite-journal").exists());
            fs::remove_dir_all(&root).expect("remove test root");
        }
    }

    #[test]
    fn ledger_append_postcommit_faults_require_inspection() {
        for fault in [
            LedgerAppendFault::Commit,
            LedgerAppendFault::Reopen,
            LedgerAppendFault::Postcondition,
        ] {
            let (root, root_hash) = ledger_append_test_root("postcommit-fault");
            let error = append_ledger_payload_with_fault(
                &ledger_append_test_payload(&root_hash),
                &root,
                fault,
            )
            .expect_err("injected post-commit fault");
            assert_eq!(error.status, KernelResponseStatus::Error);
            assert!(error.mutation_performed);
            let connection = Connection::open_with_flags(
                root.join("runs/run-1/ledger.sqlite"),
                OpenFlags::SQLITE_OPEN_READ_ONLY,
            )
            .expect("reopen ledger");
            let count: i64 = connection
                .query_row("SELECT COUNT(*) FROM commits", [], |row| row.get(0))
                .expect("commit count");
            assert_eq!(count, 2);
            drop(connection);
            fs::remove_dir_all(&root).expect("remove test root");
        }
    }

    fn persistence_bundle_test_root(label: &str) -> (PathBuf, Map<String, Value>) {
        let (root, root_hash) = ledger_append_test_root(label);
        fs::create_dir_all(root.join("runs/run-1/candidates")).expect("bundle artifact directory");
        let payload = serde_json::json!({
            "run_id": "run-1",
            "expected_tip_hash": root_hash,
            "artifacts": [{
                "artifact_id": "artifact-1",
                "artifact_type": "candidate",
                "json_value": {"nested": [1, "é"], "negative_zero": -0.0},
                "metadata": {"context": "test"},
                "filename_stem_optional": null
            }],
            "action_type": "WriteArtifact",
            "commit_payload": {"artifact_id": "artifact-1"},
            "candidate_id_optional": "candidate-1",
            "timestamp": "2026-01-01T00:00:01Z",
            "overwrite_policy": "FailIfExists",
            "recovery_policy": "ResumeExact"
        })
        .as_object()
        .expect("bundle payload")
        .clone();
        (root, payload)
    }

    #[test]
    fn persistence_bundle_precommit_faults_restore_exact_state() {
        for fault in [
            PersistenceBundleFault::IntentTempCreate,
            PersistenceBundleFault::IntentTempWrite,
            PersistenceBundleFault::IntentTempFlush,
            PersistenceBundleFault::IntentTempFsync,
            PersistenceBundleFault::IntentPublish,
            PersistenceBundleFault::IntentReadback,
            PersistenceBundleFault::SnapshotBeforeArtifact,
            PersistenceBundleFault::ArtifactTempCreate,
            PersistenceBundleFault::ArtifactTempWrite,
            PersistenceBundleFault::ArtifactTempFlush,
            PersistenceBundleFault::ArtifactTempFsync,
            PersistenceBundleFault::ArtifactPublish,
            PersistenceBundleFault::ArtifactDirectoryFsync,
            PersistenceBundleFault::ArtifactReadback,
            PersistenceBundleFault::ArtifactAfterPublish,
            PersistenceBundleFault::SnapshotBeforeSidecar,
            PersistenceBundleFault::SidecarTempCreate,
            PersistenceBundleFault::SidecarTempWrite,
            PersistenceBundleFault::SidecarTempFlush,
            PersistenceBundleFault::SidecarTempFsync,
            PersistenceBundleFault::SidecarPublish,
            PersistenceBundleFault::SidecarDirectoryFsync,
            PersistenceBundleFault::SidecarReadback,
            PersistenceBundleFault::SidecarAfterPublish,
            PersistenceBundleFault::Insert,
            PersistenceBundleFault::Readback,
        ] {
            let (root, payload) = persistence_bundle_test_root("precommit-fault");
            let run = root.join("runs/run-1");
            let before = snapshot_bundle_tree(&run).expect("pre-call snapshot");
            let error = commit_bundle_payload_with_fault(&payload, &root, fault)
                .expect_err("injected precommit fault");
            assert!(!error.mutation_performed, "fault: {fault:?}");
            assert_eq!(
                snapshot_bundle_tree(&run).expect("post-call snapshot"),
                before,
                "fault: {fault:?}"
            );
            fs::remove_dir_all(&root).expect("remove test root");
        }
    }

    #[test]
    fn persistence_bundle_postcommit_faults_preserve_recovery_intent() {
        for fault in [
            PersistenceBundleFault::Commit,
            PersistenceBundleFault::Reopen,
            PersistenceBundleFault::IntentCleanup,
            PersistenceBundleFault::FinalPostcondition,
        ] {
            let (root, payload) = persistence_bundle_test_root("postcommit-fault");
            let error = commit_bundle_payload_with_fault(&payload, &root, fault)
                .expect_err("injected postcommit fault");
            assert!(error.mutation_performed, "fault: {fault:?}");
            assert_eq!(error.status, KernelResponseStatus::Error);
            let connection = Connection::open_with_flags(
                root.join("runs/run-1/ledger.sqlite"),
                OpenFlags::SQLITE_OPEN_READ_ONLY,
            )
            .expect("reopen ledger");
            let count: i64 = connection
                .query_row("SELECT COUNT(*) FROM commits", [], |row| row.get(0))
                .expect("commit count");
            assert_eq!(count, 2);
            drop(connection);
            let intent = root.join("runs/run-1/.factori-commit-bundle.intent.json");
            assert!(intent.is_file());
            fs::remove_dir_all(&root).expect("remove test root");
        }
    }

    #[test]
    fn persistence_bundle_uncertain_rollback_keeps_intent_for_recovery() {
        let (root, payload) = persistence_bundle_test_root("rollback-fault");
        let error =
            commit_bundle_payload_with_fault(&payload, &root, PersistenceBundleFault::Rollback)
                .expect_err("injected rollback uncertainty");
        assert_eq!(error.status, KernelResponseStatus::Error);
        assert_eq!(error.code, "persistence_bundle_rollback_uncertain");
        assert!(error.mutation_performed);
        assert!(root
            .join("runs/run-1/.factori-commit-bundle.intent.json")
            .is_file());
        let connection = Connection::open_with_flags(
            root.join("runs/run-1/ledger.sqlite"),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .expect("reopen ledger");
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM commits", [], |row| row.get(0))
            .expect("commit count");
        assert_eq!(count, 1);
        drop(connection);
        fs::remove_dir_all(&root).expect("remove test root");
    }

    #[test]
    fn persistence_bundle_recovery_publish_failure_reports_mutation() {
        let (root, payload) = persistence_bundle_test_root("recovery-publish-fault");
        let run = root.join("runs/run-1");
        let connection = Connection::open_with_flags(
            run.join("ledger.sqlite"),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .expect("open ledger");
        let rows = load_raw_ledger_rows(&connection).expect("ledger rows");
        let plan = build_bundle_plan(&payload, &root, &rows).expect("bundle plan");
        drop(connection);
        fs::write(
            run.join(".factori-commit-bundle.intent.json"),
            bundle_intent_bytes(&plan, &root),
        )
        .expect("write exact intent");
        let before = snapshot_bundle_tree(&run).expect("recovery snapshot");

        let error = commit_bundle_payload_with_fault(
            &payload,
            &root,
            PersistenceBundleFault::ArtifactAfterPublish,
        )
        .expect_err("injected recovery publication fault");

        assert_eq!(error.status, KernelResponseStatus::Error);
        assert_eq!(error.code, "persistence_bundle_recovery_conflict");
        assert!(error.mutation_performed);
        assert_eq!(
            snapshot_bundle_tree(&run).expect("restored snapshot"),
            before
        );
        fs::remove_dir_all(&root).expect("remove test root");
    }

    fn artifact_link_test_root(label: &str) -> (PathBuf, Map<String, Value>) {
        let (root, root_hash) = ledger_append_test_root(label);
        let artifact_path = root.join("runs/run-1/candidates/artifact-1.json");
        fs::create_dir_all(artifact_path.parent().expect("artifact parent"))
            .expect("artifact directory");
        let artifact_bytes = b"{\"value\":1}\n";
        fs::write(&artifact_path, artifact_bytes).expect("artifact bytes");
        let content_hash = sha256_hex(artifact_bytes);
        let mut hash_artifact = serde_json::json!({
            "id": "artifact-1",
            "type": "candidate",
            "path": "runs/run-1/candidates/artifact-1.json",
            "content_hash": content_hash,
            "producing_commit_hash": "<self>",
            "metadata": {"format": "json"}
        });
        let mut commit = WireLedgerCommit {
            commit_hash: String::new(),
            parent_hash: Some(root_hash.clone()),
            run_id: "run-1".to_owned(),
            candidate_id: None,
            action_type: "WriteArtifact".to_owned(),
            payload: Map::new(),
            artifact_refs: vec![hash_artifact.clone()],
            timestamp: "2026-01-01T00:00:00Z".to_owned(),
        };
        let commit_hash = compute_wire_commit_hash(&commit).expect("artifact commit hash");
        commit.commit_hash = commit_hash.clone();
        hash_artifact
            .as_object_mut()
            .expect("artifact object")
            .insert(
                "producing_commit_hash".to_owned(),
                Value::String(commit_hash.clone()),
            );
        let artifact_json =
            canonical_json(&Value::Array(vec![hash_artifact])).expect("canonical artifact refs");
        let ledger_path = root.join("runs/run-1/ledger.sqlite");
        let connection = Connection::open(&ledger_path).expect("test ledger");
        connection
            .execute(
                "INSERT INTO commits VALUES (?1, ?2, 'run-1', NULL, 'WriteArtifact', '{}', ?3, '2026-01-01T00:00:00Z')",
                params![commit_hash, root_hash, artifact_json],
            )
            .expect("artifact commit");
        drop(connection);
        let payload = serde_json::json!({
            "run_id": "run-1",
            "expected_ledger_tip_hash": commit_hash,
            "artifact": {
                "id": "artifact-1",
                "type": "candidate",
                "path": "runs/run-1/candidates/artifact-1.json",
                "content_hash": content_hash,
                "producing_commit_hash": null,
                "metadata": {"format": "json"}
            },
            "producing_commit_hash": commit_hash,
            "overwrite_policy": "FailIfExists"
        })
        .as_object()
        .expect("link payload")
        .clone();
        (root, payload)
    }

    #[test]
    fn artifact_link_prepublication_faults_leave_no_sidecar_or_temp() {
        for fault in [
            ArtifactLinkFault::TempCreate,
            ArtifactLinkFault::TempWrite,
            ArtifactLinkFault::TempFlush,
            ArtifactLinkFault::TempFsync,
            ArtifactLinkFault::SnapshotBeforePublish,
            ArtifactLinkFault::Publish,
        ] {
            let (root, payload) = artifact_link_test_root("link-prepublish");
            let error = link_artifact_payload_with_fault(&payload, &root, fault)
                .expect_err("injected prepublication failure");
            assert_eq!(error.status, KernelResponseStatus::Rejected);
            assert!(!error.mutation_performed);
            let directory = root.join("runs/run-1/candidates");
            assert!(!directory.join("artifact-1.json.meta.json").exists());
            assert_eq!(
                fs::read_dir(&directory)
                    .expect("artifact directory")
                    .count(),
                1
            );
            fs::remove_dir_all(root).expect("remove test root");
        }
    }

    #[test]
    fn artifact_link_postpublication_faults_require_inspection() {
        for fault in [
            ArtifactLinkFault::DirectoryFsync,
            ArtifactLinkFault::FinalRead,
            ArtifactLinkFault::SnapshotAfterPublish,
        ] {
            let (root, payload) = artifact_link_test_root("link-postpublish");
            let error = link_artifact_payload_with_fault(&payload, &root, fault)
                .expect_err("injected postpublication failure");
            assert_eq!(error.status, KernelResponseStatus::Error);
            assert!(error.mutation_performed);
            assert!(root
                .join("runs/run-1/candidates/artifact-1.json.meta.json")
                .is_file());
            fs::remove_dir_all(root).expect("remove test root");
        }
    }

    #[test]
    fn artifact_link_cleanup_fault_requires_inspection() {
        let (root, payload) = artifact_link_test_root("link-cleanup");
        let error =
            link_artifact_payload_with_fault(&payload, &root, ArtifactLinkFault::TempCleanup)
                .expect_err("cleanup fault requires inspection");
        assert_eq!(error.status, KernelResponseStatus::Error);
        assert!(error.mutation_performed);
        assert_eq!(error.code, "artifact_link_temp_cleanup_warning");
        assert!(root
            .join("runs/run-1/candidates/artifact-1.json.meta.json")
            .is_file());
        fs::remove_dir_all(root).expect("remove test root");
    }
}
