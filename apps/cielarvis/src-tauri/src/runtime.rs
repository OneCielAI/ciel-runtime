use reqwest::{Client, Method};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::time::Duration;
use url::Url;

use crate::browser_mcp::BrowserMcpStatus;
use crate::terminal::TerminalSpawnRequest;

#[derive(Clone, Debug, Deserialize)]
pub struct RuntimeConnection {
    pub endpoint: String,
    #[serde(default)]
    pub token: String,
    #[serde(default)]
    pub workspace: String,
}

#[derive(Debug, Serialize)]
pub struct RuntimeSnapshot {
    connected: bool,
    endpoint: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    channel: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    runtime: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tui: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    speech: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    speech_config: Option<Value>,
}

#[derive(Debug, Serialize)]
pub struct BootstrapPlan {
    endpoint: String,
    runtime: TerminalSpawnRequest,
    #[serde(skip_serializing_if = "Option::is_none")]
    speech_status: Option<TerminalSpawnRequest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    speech_login: Option<TerminalSpawnRequest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    speech_deploy: Option<TerminalSpawnRequest>,
}

fn normalized_workspace(value: &str) -> String {
    let path = PathBuf::from(value);
    path.canonicalize()
        .unwrap_or(path)
        .to_string_lossy()
        .to_lowercase()
}

fn workspace_has_launch_history(workspace: &str) -> bool {
    let Some(path) = std::env::var("APPDATA").ok().map(PathBuf::from) else {
        return false;
    };
    let path = path.join("ciel-runtime").join("launch-state.json");
    let Ok(payload) = std::fs::read(path) else {
        return false;
    };
    let Ok(state) = serde_json::from_slice::<Value>(&payload) else {
        return false;
    };
    let target = normalized_workspace(workspace);
    state
        .get("by_cwd")
        .and_then(Value::as_object)
        .map(|records| {
            records.iter().any(|(key, value)| {
                normalized_workspace(key) == target
                    || value
                        .get("cwd")
                        .and_then(Value::as_str)
                        .map(normalized_workspace)
                        .as_deref()
                        == Some(target.as_str())
            })
        })
        .unwrap_or(false)
}

fn runtime_passthrough(has_launch_history: bool, browser_mcp_config: Option<&Path>) -> String {
    let mut values = Vec::new();
    if has_launch_history {
        values.push("--continue".to_string());
    }
    if let Some(path) = browser_mcp_config {
        values.push("--mcp-config".to_string());
        values.push(format!("'{}'", path.to_string_lossy().replace('\'', "''")));
    }
    if values.is_empty() {
        String::new()
    } else {
        format!(" -- {}", values.join(" "))
    }
}

fn workspace_router_registry() -> Option<Value> {
    let path = PathBuf::from(std::env::var("APPDATA").ok()?)
        .join("ciel-runtime")
        .join("workspace-router-ports.json");
    serde_json::from_slice(&std::fs::read(path).ok()?).ok()
}

fn select_local_workspace_port(base_port: u16, workspace: &str) -> Result<u16, String> {
    let target = normalized_workspace(workspace);
    let registry = workspace_router_registry();
    let records = registry
        .as_ref()
        .and_then(|value| value.get("workspaces"))
        .and_then(Value::as_object);
    let mut reserved = HashSet::new();
    if let Some(records) = records {
        for record in records.values().filter_map(Value::as_object) {
            let port = record
                .get("port")
                .and_then(Value::as_u64)
                .and_then(|port| u16::try_from(port).ok());
            if let Some(port) = port {
                reserved.insert(port);
                let owner = record
                    .get("workspace")
                    .and_then(Value::as_str)
                    .map(normalized_workspace);
                if owner.as_deref() == Some(target.as_str()) {
                    return Ok(port);
                }
            }
        }
    }
    let maximum = base_port.saturating_add(31);
    (base_port..=maximum)
        .find(|port| !reserved.contains(port) && TcpListener::bind(("127.0.0.1", *port)).is_ok())
        .ok_or_else(|| {
            format!(
                "No free Ciel Runtime port is available for {workspace} in {base_port}-{maximum}"
            )
        })
}

fn normalized_base(connection: &RuntimeConnection) -> Result<Url, String> {
    let mut url = Url::parse(connection.endpoint.trim()).map_err(|error| error.to_string())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("Runtime endpoint must be an absolute http(s) URL".into());
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("Runtime endpoint cannot contain credentials, query, or fragment".into());
    }
    let normalized_path = url.path().trim_end_matches('/').to_owned();
    url.set_path(&normalized_path);
    Ok(url)
}

fn endpoint_url(connection: &RuntimeConnection, path: &str) -> Result<Url, String> {
    let mut url = normalized_base(connection)?;
    let (pathname, query) = path.split_once('?').unwrap_or((path, ""));
    url.set_path(pathname);
    url.set_query((!query.is_empty()).then_some(query));
    Ok(url)
}

fn client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(25))
        .build()
        .map_err(|error| error.to_string())
}

async fn request_json(
    connection: &RuntimeConnection,
    method: Method,
    path: &str,
    body: Option<Value>,
) -> Result<Value, String> {
    let url = endpoint_url(connection, path)?;
    let mut request = client()?
        .request(method, url)
        .header("accept", "application/json");
    if !connection.token.trim().is_empty() {
        request = request.bearer_auth(connection.token.trim());
    }
    if let Some(payload) = body {
        request = request.json(&payload);
    }
    let response = request.send().await.map_err(|error| error.to_string())?;
    let status = response.status();
    let text = response.text().await.map_err(|error| error.to_string())?;
    let payload: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({ "error": text }));
    if !status.is_success() {
        return Err(format!(
            "HTTP {status}: {}",
            payload.get("error").unwrap_or(&payload)
        ));
    }
    Ok(payload)
}

#[tauri::command]
pub async fn runtime_discover(connection: RuntimeConnection) -> RuntimeSnapshot {
    let endpoint = connection.endpoint.trim_end_matches('/').to_owned();
    let channel = match request_json(&connection, Method::GET, "/ca/channel/health", None).await {
        Ok(payload) => payload,
        Err(error) => {
            return RuntimeSnapshot {
                connected: false,
                endpoint,
                error: Some(error),
                channel: None,
                runtime: None,
                tui: None,
                speech: None,
                speech_config: None,
            };
        }
    };
    let runtime = request_json(&connection, Method::GET, "/health", None)
        .await
        .ok();
    let tui = request_json(&connection, Method::GET, "/ca/tui/status", None)
        .await
        .ok();
    let speech = request_json(&connection, Method::GET, "/ca/speech/health", None)
        .await
        .ok();
    let speech_config = request_json(&connection, Method::GET, "/ca/speech/config", None)
        .await
        .ok();
    RuntimeSnapshot {
        connected: true,
        endpoint,
        error: None,
        channel: Some(channel),
        runtime,
        tui,
        speech,
        speech_config,
    }
}

#[tauri::command]
pub async fn runtime_wait_messages(
    connection: RuntimeConnection,
    after: u64,
    channel: String,
) -> Result<Value, String> {
    request_json(
        &connection,
        Method::GET,
        &channel_wait_path(after, &channel),
        None,
    )
    .await
}

fn channel_wait_path(after: u64, channel: &str) -> String {
    format!(
        "/ca/channel/wait?after={after}&channel={}&recipient=web&timeout=20",
        url::form_urlencoded::byte_serialize(channel.as_bytes()).collect::<String>()
    )
}

#[tauri::command]
pub async fn runtime_send_message(
    connection: RuntimeConnection,
    payload: Value,
) -> Result<Value, String> {
    request_json(
        &connection,
        Method::POST,
        "/ca/channel/messages",
        Some(payload),
    )
    .await
}

#[tauri::command]
pub async fn runtime_transcribe_audio(
    connection: RuntimeConnection,
    audio_base64: String,
    content_type: String,
    model: Option<String>,
    language: Option<String>,
) -> Result<Value, String> {
    if audio_base64.len() > 700_000_000 {
        return Err("Voice recording exceeds the CIELARVIS transport limit".into());
    }
    request_json(
        &connection,
        Method::POST,
        "/v1/audio/transcriptions",
        Some(json!({
            "audio_base64": audio_base64,
            "filename": "cielarvis-recording.webm",
            "content_type": if content_type.trim().is_empty() { "audio/webm" } else { &content_type },
            "model": model,
            "language": language,
        })),
    )
    .await
}

fn powershell_request(
    title: &str,
    kind: &str,
    args: Vec<String>,
    cwd: &str,
) -> TerminalSpawnRequest {
    TerminalSpawnRequest {
        title: title.into(),
        kind: kind.into(),
        program: "powershell.exe".into(),
        args,
        cwd: (!cwd.trim().is_empty()).then(|| cwd.to_owned()),
        cols: 132,
        rows: 24,
    }
}

fn speech_script() -> Option<PathBuf> {
    if let Ok(home) = std::env::var("CIEL_RUNTIME_HOME") {
        let candidate = PathBuf::from(home)
            .join("scripts")
            .join("deploy_colab_speech.ps1");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    std::env::var("USERPROFILE")
        .ok()
        .map(PathBuf::from)
        .and_then(|home| {
            let candidate = home
                .join(".local")
                .join("share")
                .join("ciel-runtime")
                .join("scripts")
                .join("deploy_colab_speech.ps1");
            candidate.is_file().then_some(candidate)
        })
}

pub fn browser_mcp_config_path() -> PathBuf {
    std::env::temp_dir().join(format!("cielarvis-browser-mcp-{}.json", std::process::id()))
}

fn write_browser_mcp_config(status: Option<&BrowserMcpStatus>) -> Result<Option<PathBuf>, String> {
    let Some(status) = status.filter(|status| status.ready) else {
        return Ok(None);
    };
    let (Some(endpoint), Some(authorization)) = (&status.endpoint, &status.authorization) else {
        return Ok(None);
    };
    let path = browser_mcp_config_path();
    let payload = json!({
        "mcpServers": {
            "cielarvis-browser": {
                "type": "http",
                "url": endpoint,
                "headers": { "Authorization": authorization }
            },
            "ciel-runtime-router": {
                "type": "http",
                "url": endpoint,
                "headers": { "Authorization": authorization }
            }
        }
    });
    let temporary = path.with_extension("json.tmp");
    std::fs::write(
        &temporary,
        serde_json::to_vec_pretty(&payload).map_err(|error| error.to_string())?,
    )
    .map_err(|error| format!("Could not write CIELARVIS Browser MCP config: {error}"))?;
    if path.exists() {
        std::fs::remove_file(&path)
            .map_err(|error| format!("Could not replace CIELARVIS Browser MCP config: {error}"))?;
    }
    std::fs::rename(&temporary, &path)
        .map_err(|error| format!("Could not activate CIELARVIS Browser MCP config: {error}"))?;
    Ok(Some(path))
}

#[tauri::command]
pub fn bootstrap_plan(
    connection: RuntimeConnection,
    browser_mcp: Option<BrowserMcpStatus>,
) -> Result<BootstrapPlan, String> {
    let url = normalized_base(&connection)?;
    let requested_port = url
        .port_or_known_default()
        .ok_or("Runtime endpoint has no usable port")?;
    let host = url.host_str().unwrap_or_default();
    if host != "127.0.0.1" && host != "localhost" && host != "::1" {
        return Err(format!(
            "Automatic Runtime bootstrap is only available for a local endpoint, not {host}"
        ));
    }
    let workspace = if connection.workspace.trim().is_empty() {
        std::env::var("USERPROFILE").unwrap_or_default()
    } else {
        connection.workspace.clone()
    };
    if workspace.trim().is_empty() || !Path::new(&workspace).is_dir() {
        return Err(format!("Runtime workspace does not exist: {workspace}"));
    }
    let port = select_local_workspace_port(requested_port, &workspace)?;
    let endpoint = format!("{}://127.0.0.1:{port}", url.scheme());
    let browser_mcp_config = write_browser_mcp_config(browser_mcp.as_ref())?;
    let resume_existing = workspace_has_launch_history(&workspace);
    let runtime_passthrough = runtime_passthrough(resume_existing, browser_mcp_config.as_deref());
    let resume_status = if resume_existing {
        "Resume mode: --continue (workspace launch history found)"
    } else {
        "Resume mode: new session (first workspace launch)"
    };
    let runtime_script = format!(
        "$ErrorActionPreference='Stop'; Clear-Host; Write-Host 'CIELARVIS BOOT CONSOLE' -ForegroundColor Cyan; Write-Host 'Workspace: ' (Get-Location).Path; Write-Host 'Endpoint:  {endpoint}'; Write-Host '{resume_status}' -ForegroundColor DarkCyan; if (-not (Get-Command ciel-runtime -ErrorAction SilentlyContinue)) {{ Write-Host 'Installing Ciel Runtime nightly...' -ForegroundColor Yellow; npm install -g @oneciel-ai/ciel-runtime@nightly --force }}; $runtimeCli=(Get-Command ciel-runtime -ErrorAction Stop).Source; $env:CIEL_RUNTIME_ROUTER_PORT='{port}'; Write-Host 'Starting the last actually used Runtime for this workspace...' -ForegroundColor Green; & $runtimeCli --ca-web-port {port} --ca-runtime=last --ca-no-self-update-check{runtime_passthrough}; $runtimeExit=$LASTEXITCODE; Write-Host ''; Write-Host \"Ciel Runtime stopped (exit $runtimeExit). Review the output above, then use RETRY BOOT.\" -ForegroundColor Yellow"
    );
    let runtime = powershell_request(
        "Ciel Runtime",
        "runtime",
        vec![
            "-NoLogo".into(),
            "-NoExit".into(),
            "-ExecutionPolicy".into(),
            "Bypass".into(),
            "-Command".into(),
            runtime_script,
        ],
        &workspace,
    );
    let (speech_status, speech_login, speech_deploy) = if let Some(script) = speech_script() {
        let script = script.to_string_lossy().to_string();
        let make = |title: &str, action: &str| {
            powershell_request(
                title,
                "speech",
                vec![
                    "-NoLogo".into(),
                    "-NoExit".into(),
                    "-ExecutionPolicy".into(),
                    "Bypass".into(),
                    "-File".into(),
                    script.clone(),
                    "-Action".into(),
                    action.into(),
                ],
                &workspace,
            )
        };
        (
            Some(make("Voice status", "Status")),
            Some(make("Voice login", "Login")),
            Some(make("Voice deploy", "Deploy")),
        )
    } else {
        (None, None, None)
    };
    Ok(BootstrapPlan {
        endpoint,
        runtime,
        speech_status,
        speech_login,
        speech_deploy,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn connection(endpoint: &str) -> RuntimeConnection {
        RuntimeConnection {
            endpoint: endpoint.into(),
            token: String::new(),
            workspace: String::new(),
        }
    }

    #[test]
    fn endpoint_query_is_not_encoded_into_the_path() {
        let url = endpoint_url(
            &connection("http://127.0.0.1:6969"),
            "/ca/channel/wait?after=4&timeout=20",
        )
        .unwrap();
        assert_eq!(url.path(), "/ca/channel/wait");
        assert_eq!(url.query(), Some("after=4&timeout=20"));
    }

    #[test]
    fn endpoint_rejects_embedded_credentials() {
        let error = normalized_base(&connection("http://secret@127.0.0.1:6969")).unwrap_err();
        assert!(error.contains("credentials"));
    }

    #[test]
    fn channel_wait_subscribes_to_web_delivery_replies() {
        let path = channel_wait_path(10, "cielarvis-session/a");
        assert_eq!(
            path,
            "/ca/channel/wait?after=10&channel=cielarvis-session%2Fa&recipient=web&timeout=20"
        );
    }

    #[test]
    fn runtime_bootstrap_bypasses_policy_only_for_the_child_shell() {
        let plan = bootstrap_plan(connection("http://127.0.0.1:6969"), None).unwrap();
        assert_eq!(plan.runtime.program, "powershell.exe");
        assert!(
            plan.runtime
                .args
                .windows(2)
                .any(|values| { values == ["-ExecutionPolicy", "Bypass"] })
        );
        let command = plan.runtime.args.last().unwrap();
        assert!(command.contains("$env:CIEL_RUNTIME_ROUTER_PORT="));
        assert!(command.contains("& $runtimeCli --ca-web-port"));
        assert!(command.contains("--ca-runtime=last"));
        assert!(!command.contains("--ca-menu"));
        assert!(!command.contains("Start-Job"));
    }

    #[test]
    fn resume_and_browser_config_share_one_runtime_passthrough_boundary() {
        let rendered = runtime_passthrough(true, Some(Path::new("C:\\Temp\\browser.json")));
        assert_eq!(
            rendered,
            " -- --continue --mcp-config 'C:\\Temp\\browser.json'"
        );
        assert_eq!(runtime_passthrough(false, None), "");
    }

    #[test]
    fn browser_mcp_config_keeps_session_authorization_in_the_config_file() {
        let path = write_browser_mcp_config(Some(&BrowserMcpStatus {
            ready: true,
            endpoint: Some("http://127.0.0.1:45678/mcp".into()),
            authorization: Some("Bearer secret-session-token".into()),
            error: None,
        }))
        .unwrap()
        .unwrap();
        let payload: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(payload["mcpServers"]["cielarvis-browser"]["type"], "http");
        assert_eq!(payload["mcpServers"]["ciel-runtime-router"]["type"], "http");
        assert_eq!(
            payload["mcpServers"]["cielarvis-browser"]["headers"]["Authorization"],
            "Bearer secret-session-token"
        );
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn local_port_selection_skips_a_bound_port() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let base = listener.local_addr().unwrap().port();
        if base < u16::MAX {
            let selected =
                select_local_workspace_port(base, "C:\\definitely-new-cielarvis-workspace")
                    .unwrap();
            assert_ne!(selected, base);
        }
    }
}
