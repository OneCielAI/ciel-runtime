use std::sync::{Arc, Mutex};

use axum::{
    Router,
    extract::{Request, State as AxumState},
    http::{StatusCode, header::AUTHORIZATION},
    middleware::{self, Next},
    response::Response,
};
use rmcp::{
    ErrorData, ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{CallToolResult, ContentBlock, ServerCapabilities, ServerInfo},
    tool, tool_handler, tool_router,
    transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
    },
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::{AppHandle, Manager};
use uuid::Uuid;

use crate::browser::{self, BrowserState, KeyboardInput, PointerInput};

const MCP_PATH: &str = "/mcp";

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct BrowserMcpStatus {
    pub ready: bool,
    pub endpoint: Option<String>,
    pub authorization: Option<String>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Default)]
struct RuntimeBridge {
    endpoint: String,
    token: String,
}

#[derive(Clone, Debug, Default)]
struct BrowserMcpShared {
    status: BrowserMcpStatus,
    runtime: RuntimeBridge,
}

#[derive(Clone, Default)]
pub struct BrowserMcpState(Arc<Mutex<BrowserMcpShared>>);

impl BrowserMcpState {
    fn replace(&self, status: BrowserMcpStatus) {
        if let Ok(mut current) = self.0.lock() {
            current.status = status;
        }
    }

    fn current(&self) -> Result<BrowserMcpStatus, String> {
        self.0
            .lock()
            .map(|state| state.status.clone())
            .map_err(|_| "Browser MCP status lock was poisoned".into())
    }

    fn configure_runtime(&self, endpoint: String, token: String) -> Result<(), String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Browser MCP state lock was poisoned".to_string())?;
        state.runtime = RuntimeBridge { endpoint, token };
        Ok(())
    }

    fn runtime(&self) -> Result<RuntimeBridge, String> {
        self.0
            .lock()
            .map(|state| state.runtime.clone())
            .map_err(|_| "Browser MCP state lock was poisoned".into())
    }
}

#[tauri::command]
pub fn browser_mcp_status(
    state: tauri::State<'_, BrowserMcpState>,
) -> Result<BrowserMcpStatus, String> {
    state.current()
}

#[tauri::command]
pub fn browser_mcp_configure_runtime(
    state: tauri::State<'_, BrowserMcpState>,
    endpoint: String,
    token: String,
) -> Result<(), String> {
    let url = url::Url::parse(endpoint.trim()).map_err(|error| error.to_string())?;
    if !matches!(url.scheme(), "http" | "https")
        || !matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
    {
        return Err("CIELARVIS MCP replies require a loopback Runtime endpoint".into());
    }
    state.configure_runtime(endpoint.trim_end_matches('/').to_string(), token)
}

#[derive(Clone)]
struct BrowserMcpServer {
    app: AppHandle,
    state: BrowserMcpState,
    tool_router: ToolRouter<Self>,
}

impl BrowserMcpServer {
    fn new(app: AppHandle, state: BrowserMcpState) -> Self {
        Self {
            app,
            state,
            tool_router: Self::tool_router(),
        }
    }

    fn error(error: String) -> ErrorData {
        ErrorData::internal_error(error, None)
    }

    fn text_result(value: impl Serialize) -> Result<CallToolResult, ErrorData> {
        let text = serde_json::to_string(&value)
            .map_err(|error| Self::error(format!("Could not serialize browser result: {error}")))?;
        Ok(CallToolResult::success(vec![ContentBlock::text(text)]))
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
struct OpenTabArgs {
    #[schemars(description = "Optional http(s) URL; defaults to the browser home page")]
    url: Option<String>,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct TabArgs {
    tab_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct NavigateArgs {
    tab_id: String,
    url: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct EvaluateArgs {
    tab_id: String,
    #[schemars(description = "JavaScript function body executed in an async page context")]
    script: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct PointerArgs {
    tab_id: String,
    input: PointerInput,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct KeyboardArgs {
    tab_id: String,
    input: KeyboardInput,
}

#[derive(Debug, Deserialize, JsonSchema)]
struct SendMessageArgs {
    channel: String,
    thread_id: String,
    parent_id: String,
    reply_token: String,
    kind: Option<String>,
    message: Option<String>,
    response: Option<Value>,
}

#[tool_router]
impl BrowserMcpServer {
    #[tool(
        name = "send_message",
        description = "Send one correlated acknowledgement or reply to the active CIELARVIS chat route"
    )]
    async fn send_message(
        &self,
        Parameters(args): Parameters<SendMessageArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let channel = args.channel.trim();
        let thread_id = args.thread_id.trim();
        let parent_id = args.parent_id.trim();
        let reply_token = args.reply_token.trim();
        let kind = args
            .kind
            .as_deref()
            .unwrap_or("reply")
            .trim()
            .to_lowercase();
        if !channel.starts_with("cielarvis-")
            || thread_id.is_empty()
            || parent_id.parse::<u64>().ok().filter(|id| *id > 0).is_none()
            || reply_token.is_empty()
            || !matches!(kind.as_str(), "ack" | "reply")
        {
            return Err(Self::error(
                "A valid current CIELARVIS channel, thread_id, parent_id, reply_token, and ack/reply kind are required".into(),
            ));
        }
        let response = args.response.filter(Value::is_object);
        let message = args
            .message
            .unwrap_or_else(|| response_text(response.as_ref()))
            .trim()
            .to_string();
        if message.is_empty() {
            return Err(Self::error(
                "A web reply message or response object is required".into(),
            ));
        }
        let runtime = self.state.runtime().map_err(Self::error)?;
        if runtime.endpoint.is_empty() {
            return Err(Self::error(
                "The CIELARVIS Runtime reply bridge is not configured".into(),
            ));
        }
        let mut request = reqwest::Client::new()
            .post(format!("{}/ca/channel/messages", runtime.endpoint))
            .json(&json!({
                "channel": channel,
                "sender_id": "claude-code",
                "recipients": ["web"],
                "delivery": ["web"],
                "thread_id": thread_id,
                "parent_id": parent_id,
                "kind": kind,
                "message": message,
                "meta": {
                    "source": "ciel-runtime-router-tool",
                    "web_reply_token": reply_token,
                    "web_response": response,
                },
            }));
        if !runtime.token.trim().is_empty() {
            request = request.bearer_auth(runtime.token.trim());
        }
        let response = request
            .send()
            .await
            .map_err(|error| Self::error(error.to_string()))?;
        let status = response.status();
        let payload: Value = response
            .json()
            .await
            .map_err(|error| Self::error(error.to_string()))?;
        if !status.is_success() {
            return Err(Self::error(format!(
                "Runtime reply failed with HTTP {status}: {payload}"
            )));
        }
        Self::text_result(payload)
    }

    #[tool(
        name = "browser_tabs_list",
        description = "List all isolated CIELARVIS browser tabs"
    )]
    fn tabs_list(&self) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(browser::browser_list_tabs(state).map_err(Self::error)?)
    }

    #[tool(
        name = "browser_tab_open",
        description = "Open a new isolated browser tab"
    )]
    async fn tab_open(
        &self,
        Parameters(OpenTabArgs { url }): Parameters<OpenTabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let window = self
            .app
            .get_window("main")
            .ok_or_else(|| Self::error("CIELARVIS main window is unavailable".into()))?;
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_create_tab(self.app.clone(), window, state, url)
                .await
                .map_err(Self::error)?,
        )
    }

    #[tool(name = "browser_tab_close", description = "Close a browser tab")]
    fn tab_close(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        browser::browser_close_tab(self.app.clone(), state, tab_id).map_err(Self::error)?;
        Self::text_result(json!({ "ok": true }))
    }

    #[tool(name = "browser_tab_activate", description = "Activate a browser tab")]
    fn tab_activate(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_activate_tab(self.app.clone(), state, tab_id).map_err(Self::error)?,
        )
    }

    #[tool(
        name = "browser_navigate",
        description = "Navigate a tab to an http(s) URL"
    )]
    fn navigate(
        &self,
        Parameters(NavigateArgs { tab_id, url }): Parameters<NavigateArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_navigate(self.app.clone(), state, tab_id, url).map_err(Self::error)?,
        )
    }

    #[tool(name = "browser_back", description = "Navigate a tab backward")]
    async fn back(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_back(self.app.clone(), state, tab_id)
                .await
                .map_err(Self::error)?,
        )
    }

    #[tool(name = "browser_forward", description = "Navigate a tab forward")]
    async fn forward(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_forward(self.app.clone(), state, tab_id)
                .await
                .map_err(Self::error)?,
        )
    }

    #[tool(name = "browser_reload", description = "Reload a browser tab")]
    fn reload(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        browser::browser_reload(self.app.clone(), state, tab_id).map_err(Self::error)?;
        Self::text_result(json!({ "ok": true }))
    }

    #[tool(
        name = "browser_snapshot",
        description = "Read bounded page text, links, controls, viewport, and a fresh frame id"
    )]
    async fn snapshot(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_snapshot(self.app.clone(), state, tab_id)
                .await
                .map_err(Self::error)?,
        )
    }

    #[tool(
        name = "browser_javascript_evaluate",
        description = "Evaluate JavaScript in the isolated page and return its JSON value"
    )]
    async fn javascript_evaluate(
        &self,
        Parameters(EvaluateArgs { tab_id, script }): Parameters<EvaluateArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_evaluate(self.app.clone(), state, tab_id, script)
                .await
                .map_err(Self::error)?,
        )
    }

    #[tool(
        name = "browser_screenshot",
        description = "Capture the visible browser viewport as JPEG plus frame metadata"
    )]
    async fn screenshot(
        &self,
        Parameters(TabArgs { tab_id }): Parameters<TabArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        let mut result = browser::browser_screenshot(self.app.clone(), state, tab_id)
            .await
            .map_err(Self::error)?;
        let data = result
            .get("data")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| Self::error("Browser screenshot did not contain image data".into()))?;
        result.as_object_mut().map(|object| object.remove("data"));
        Ok(CallToolResult::success(vec![
            ContentBlock::image(data, "image/jpeg"),
            ContentBlock::text(result.to_string()),
        ]))
    }

    #[tool(
        name = "browser_pointer",
        description = "Move, click, drag, or wheel the pointer using a current screenshot/snapshot frame id"
    )]
    async fn pointer(
        &self,
        Parameters(PointerArgs { tab_id, input }): Parameters<PointerArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_pointer(self.app.clone(), state, tab_id, input)
                .await
                .map_err(Self::error)?,
        )
    }

    #[tool(
        name = "browser_keyboard",
        description = "Type text or dispatch key down/up/press using a current frame id"
    )]
    async fn keyboard(
        &self,
        Parameters(KeyboardArgs { tab_id, input }): Parameters<KeyboardArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let state = self.app.state::<BrowserState>();
        Self::text_result(
            browser::browser_keyboard(self.app.clone(), state, tab_id, input)
                .await
                .map_err(Self::error)?,
        )
    }
}

fn response_text(response: Option<&Value>) -> String {
    let Some(response) = response.and_then(Value::as_object) else {
        return String::new();
    };
    ["overview", "details", "spoken"]
        .into_iter()
        .filter_map(|key| response.get(key).and_then(Value::as_str))
        .map(str::trim)
        .find(|value| !value.is_empty())
        .unwrap_or_default()
        .to_string()
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for BrowserMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build()).with_instructions(
            "Control the isolated CIELARVIS browser and return correlated Ciel Chat replies. Take a snapshot or screenshot before coordinate input and use its current frame_id.",
        )
    }
}

async fn require_bearer(
    AxumState(expected): AxumState<Arc<str>>,
    request: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    let supplied = request
        .headers()
        .get(AUTHORIZATION)
        .and_then(|value| value.to_str().ok());
    if supplied != Some(expected.as_ref()) {
        return Err(StatusCode::UNAUTHORIZED);
    }
    Ok(next.run(request).await)
}

pub async fn serve(app: AppHandle, status: BrowserMcpState) {
    let token = format!(
        "Bearer {}{}",
        Uuid::new_v4().simple(),
        Uuid::new_v4().simple()
    );
    let listener = match tokio::net::TcpListener::bind(("127.0.0.1", 0)).await {
        Ok(listener) => listener,
        Err(error) => {
            status.replace(BrowserMcpStatus {
                error: Some(format!("Could not bind Browser MCP server: {error}")),
                ..Default::default()
            });
            return;
        }
    };
    let address = match listener.local_addr() {
        Ok(address) => address,
        Err(error) => {
            status.replace(BrowserMcpStatus {
                error: Some(format!("Could not inspect Browser MCP address: {error}")),
                ..Default::default()
            });
            return;
        }
    };
    let app_for_service = app.clone();
    let state_for_service = status.clone();
    let service = StreamableHttpService::new(
        move || {
            Ok(BrowserMcpServer::new(
                app_for_service.clone(),
                state_for_service.clone(),
            ))
        },
        LocalSessionManager::default().into(),
        StreamableHttpServerConfig::default()
            .with_legacy_session_mode(false)
            .with_json_response(true)
            .with_allowed_hosts([
                "localhost".to_string(),
                "127.0.0.1".to_string(),
                format!("127.0.0.1:{}", address.port()),
            ]),
    );
    let authorization: Arc<str> = token.clone().into();
    let router =
        Router::new()
            .nest_service(MCP_PATH, service)
            .layer(middleware::from_fn_with_state(
                authorization,
                require_bearer,
            ));
    status.replace(BrowserMcpStatus {
        ready: true,
        endpoint: Some(format!("http://{address}{MCP_PATH}")),
        authorization: Some(token),
        error: None,
    });
    if let Err(error) = axum::serve(listener, router).await {
        status.replace(BrowserMcpStatus {
            error: Some(format!("Browser MCP server stopped: {error}")),
            ..Default::default()
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn bearer_auth_rejects_missing_or_wrong_tokens() {
        let next_app = Router::new().fallback(|| async { StatusCode::NO_CONTENT });
        for supplied in [None, Some("Bearer wrong")] {
            let mut request = Request::builder().uri("/mcp");
            if let Some(value) = supplied {
                request = request.header(AUTHORIZATION, value);
            }
            let response =
                middleware::from_fn_with_state(Arc::<str>::from("Bearer correct"), require_bearer);
            let router = next_app.clone().layer(response);
            use tower::ServiceExt;
            let result = router
                .oneshot(request.body(axum::body::Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(result.status(), StatusCode::UNAUTHORIZED);
        }
    }

    #[tokio::test]
    async fn bearer_auth_accepts_the_exact_process_token() {
        use tower::ServiceExt;

        let router = Router::new()
            .fallback(|| async { StatusCode::NO_CONTENT })
            .layer(middleware::from_fn_with_state(
                Arc::<str>::from("Bearer exact"),
                require_bearer,
            ));
        let request = Request::builder()
            .uri("/mcp")
            .header(AUTHORIZATION, "Bearer exact")
            .body(axum::body::Body::empty())
            .unwrap();
        assert_eq!(
            router.oneshot(request).await.unwrap().status(),
            StatusCode::NO_CONTENT
        );
    }

    #[test]
    fn publishes_the_complete_browser_control_tool_set() {
        let names: Vec<_> = BrowserMcpServer::tool_router()
            .list_all()
            .into_iter()
            .map(|tool| tool.name.to_string())
            .collect();
        for required in [
            "send_message",
            "browser_tabs_list",
            "browser_tab_open",
            "browser_navigate",
            "browser_snapshot",
            "browser_javascript_evaluate",
            "browser_screenshot",
            "browser_pointer",
            "browser_keyboard",
        ] {
            assert!(
                names.iter().any(|name| name == required),
                "missing {required}"
            );
        }
        assert_eq!(names.len(), 14);
    }

    #[test]
    fn structured_reply_prefers_overview_text() {
        assert_eq!(
            response_text(Some(&json!({ "spoken": "say", "overview": "show" }))),
            "show"
        );
    }
}
