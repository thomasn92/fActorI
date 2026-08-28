use std::ffi::OsString;
use std::io::{self, Read};
use std::path::PathBuf;

const MAX_REQUEST_BYTES: u64 = 16 * 1024 * 1024;

fn main() -> std::process::ExitCode {
    let root = match parse_root_argument(std::env::args_os().skip(1)) {
        Ok(root) => root,
        Err(message) => return emit_transport_error(message),
    };
    let mut bytes = Vec::new();
    if let Err(error) = io::stdin()
        .take(MAX_REQUEST_BYTES + 1)
        .read_to_end(&mut bytes)
    {
        return emit_transport_error(format!("could not read request: {error}"));
    }
    if bytes.len() as u64 > MAX_REQUEST_BYTES {
        return emit_transport_error(format!(
            "request exceeds the {MAX_REQUEST_BYTES}-byte transport limit"
        ));
    }
    let input = match String::from_utf8(bytes) {
        Ok(input) => input,
        Err(_) => return emit_transport_error("request must be UTF-8".to_owned()),
    };

    let (response, exit_code) = match factori_kernel::parse_and_handle_with_root(&input, root) {
        Ok(response) => (response, std::process::ExitCode::SUCCESS),
        Err(error) => (
            factori_kernel::transport_error_response(&error),
            std::process::ExitCode::from(2),
        ),
    };
    match serde_json::to_string(&response) {
        Ok(output) => {
            println!("{output}");
            exit_code
        }
        Err(error) => {
            eprintln!("internal_error: {error}");
            std::process::ExitCode::from(2)
        }
    }
}

fn parse_root_argument(
    mut arguments: impl Iterator<Item = OsString>,
) -> Result<Option<PathBuf>, String> {
    let Some(flag) = arguments.next() else {
        return Ok(None);
    };
    if flag != "--root" {
        return Err("only --root <project-root> is supported".to_owned());
    }
    let root = arguments
        .next()
        .ok_or_else(|| "--root requires a project-root path".to_owned())?;
    if arguments.next().is_some() {
        return Err("only one --root <project-root> argument is supported".to_owned());
    }
    Ok(Some(PathBuf::from(root)))
}

fn emit_transport_error(message: String) -> std::process::ExitCode {
    let error = factori_kernel::KernelError::InvalidRequest(message);
    match serde_json::to_string(&factori_kernel::transport_error_response(&error)) {
        Ok(output) => println!("{output}"),
        Err(serialization_error) => eprintln!("internal_error: {serialization_error}"),
    }
    std::process::ExitCode::from(2)
}
