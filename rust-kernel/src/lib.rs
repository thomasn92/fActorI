//! Read-only protocol and canonical-hash boundary for the future Rust kernel.
//!
//! This crate opens persisted artifacts and the SQLite ledger read-only for integrity checks. It
//! deliberately does not construct evidence capabilities, execute processes, mutate run state, or
//! make claim-authority decisions. Those operations require the contract review described in
//! `RUST_KERNEL_CONTRACT.md`.

use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::Deserializer;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};

pub const PROTOCOL_VERSION: &str = "0.82.0";
pub const KERNEL_VERSION: &str = "0.1.0-dev";

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

#[derive(Debug, serde::Serialize, serde::Deserialize)]
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
    validate_kernel_request(&request).map_err(KernelError::InvalidRequest)?;
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
    }
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

    if explicit_false
        || is_presentation_artifact(artifact)
        || artifact.artifact_type == "literature"
    {
        return Ok((
            evidence_classification_result(&classify.run_id, &artifact.id, "Context", None, false),
            Vec::new(),
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
    if parts[2] != expected_directory {
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
    reference.get("id").and_then(Value::as_str) == Some(artifact.id.as_str())
        && reference.get("path").and_then(Value::as_str) == Some(artifact.path.as_str())
        && reference.get("type").and_then(Value::as_str) == Some(artifact.artifact_type.as_str())
        && reference.get("content_hash").and_then(Value::as_str)
            == Some(artifact.content_hash.as_str())
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
    if response.mutation_performed {
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
            require_exact_keys(
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
            if !response
                .result
                .get("compatibility_only")
                .is_some_and(Value::is_boolean)
            {
                return Err("compatibility_only must be a boolean".to_owned());
            }
            if response.result.get("authority_granted") != Some(&Value::Bool(false)) {
                return Err("evidence classification cannot grant authority".to_owned());
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
            r#"{"protocol_version":"0.82.0","request_id":"r1","operation":"hash.canonical_json","mode":"DevelopmentCompatibility","payload":{"value":{"b":2,"a":1}}}"#,
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
            r#"{"protocol_version":"0.82.0","request_id":"r1","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{"protocol_name":"KernelRequestEnvelope","instance":{"protocol_version":"0.82.0","request_id":"r2","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{},"unexpected":true}}}"#,
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
            r#"{"protocol_version":"0.82.0","request_id":"r1","operation":"protocol.validate","mode":"DevelopmentCompatibility","payload":{"value":{"a":1,"a":2}}}"#,
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
}
